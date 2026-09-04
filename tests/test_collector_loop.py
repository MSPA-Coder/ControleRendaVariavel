"""O laço de coleta, exercitado sem rede, sem banco e sem ProfitChart.

Estes casos existem porque o laço passou a ser compartilhado entre o agente
remoto e a coleta local: uma regressão aqui atingiria os dois destinos ao
mesmo tempo.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time
from decimal import Decimal

import pytest

from app.collector.loop import CollectorConfiguration, run_collector_loop
from app.collector.providers import CollectorProviderManager
from app.collector.rtd import Instrument, QuoteValue
from app.collector.settings import CollectorSchedule
from app.models import CollectorMode

ABERTA = CollectorSchedule(frozenset({0, 1, 2, 3, 4}), time(9, 45), time(18, 10))
DENTRO_DA_JANELA = datetime(2026, 8, 17, 12, 45, tzinfo=UTC)
FORA_DA_JANELA = datetime(2026, 8, 17, 21, 10, tzinfo=UTC)


class _LacoInterrompidoError(Exception):
    """Interrompe o laço infinito depois dos ciclos que o caso precisa."""


class _OrigemFixa:
    def __init__(self, configuration: CollectorConfiguration) -> None:
        self.configuration_value = configuration
        self.chamadas = 0

    def configuration(self) -> CollectorConfiguration:
        self.chamadas += 1
        return self.configuration_value


class _OrigemQueFalha:
    def __init__(self) -> None:
        self.chamadas = 0

    def configuration(self) -> CollectorConfiguration:
        self.chamadas += 1
        raise RuntimeError("Comunicação com o VPS falhou.")


class _DestinoEspiao:
    destination_label = "ao destino de teste"

    def __init__(self) -> None:
        self.entregas: list[list[QuoteValue]] = []
        self.falhas: list[Exception] = []

    def publish(self, values, option_keys) -> None:
        self.entregas.append(values)

    def report_failure(self, error: Exception) -> None:
        self.falhas.append(error)


class _ProvedorFalso:
    def __init__(self) -> None:
        self.aberto = False
        self.fechamentos = 0

    def open(self) -> None:
        self.aberto = True

    def close(self) -> None:
        self.aberto = False
        self.fechamentos += 1

    def fetch(self, instruments: list[Instrument]) -> list[QuoteValue]:
        return [
            QuoteValue(item.position_id, Decimal("10"), Decimal("9"), "A", DENTRO_DA_JANELA)
            for item in instruments
        ]


class _ProfitFalso:
    def __init__(self, *, rodando: bool) -> None:
        self.rodando = rodando

    def is_running(self) -> bool:
        return self.rodando


def _configuracao(**overrides) -> CollectorConfiguration:
    valores: dict[str, object] = {
        "collector_mode": CollectorMode.DIRECT,
        "poll_interval_seconds": 5,
        "agent_check_interval_seconds": 30,
        "schedule": ABERTA,
        "instruments": (Instrument(7, "ABCD3", "B", "C"),),
    }
    valores.update(overrides)
    return CollectorConfiguration(**valores)  # type: ignore[arg-type]


def _rodar(origem, destino, detector, *, agora=DENTRO_DA_JANELA, ciclos=1):
    """Executa `ciclos` iterações e devolve o provedor usado."""
    provedor = _ProvedorFalso()
    relogio = {"agora": 0.0}
    restantes = {"ciclos": ciclos}

    def sleep(seconds: float) -> None:
        # Fiel ao real: o tempo só anda quando o laço dorme, e anda
        # exatamente até o prazo que ele mesmo calculou.
        relogio["agora"] += seconds
        restantes["ciclos"] -= 1
        if restantes["ciclos"] <= 0:
            raise _LacoInterrompidoError

    with pytest.raises(_LacoInterrompidoError):
        run_collector_loop(
            source=origem,
            sink=destino,
            providers=CollectorProviderManager(lambda _mode: provedor),
            detector=detector,
            logger=logging.getLogger("teste.collector_loop"),
            initial_schedule=ABERTA,
            initial_check_interval=30,
            monotonic=lambda: relogio["agora"],
            sleep=sleep,
            market_now=lambda: agora,
        )
    return provedor


def test_primeira_configuracao_dispara_leitura_imediata() -> None:
    destino = _DestinoEspiao()

    _rodar(_OrigemFixa(_configuracao()), destino, _ProfitFalso(rodando=True))

    assert [valor.position_id for valor in destino.entregas[0]] == [7]
    assert destino.falhas == []


def test_fora_da_agenda_nao_le_e_fecha_o_provedor() -> None:
    destino = _DestinoEspiao()

    provedor = _rodar(
        _OrigemFixa(_configuracao()),
        destino,
        _ProfitFalso(rodando=True),
        agora=FORA_DA_JANELA,
    )

    assert destino.entregas == []
    assert provedor.aberto is False


def test_profit_fechado_nao_le_e_nao_reporta_falha() -> None:
    destino = _DestinoEspiao()

    _rodar(_OrigemFixa(_configuracao()), destino, _ProfitFalso(rodando=False))

    assert destino.entregas == []
    assert destino.falhas == []


def test_origem_indisponivel_reporta_falha_e_nao_coleta() -> None:
    destino = _DestinoEspiao()

    _rodar(_OrigemQueFalha(), destino, _ProfitFalso(rodando=True))

    assert destino.entregas == []
    assert [str(erro) for erro in destino.falhas] == ["Comunicação com o VPS falhou."]


def test_coleta_pausada_nao_le_e_fecha_o_provedor() -> None:
    destino = _DestinoEspiao()

    provedor = _rodar(
        _OrigemFixa(_configuracao(paused=True)),
        destino,
        _ProfitFalso(rodando=True),
    )

    assert destino.entregas == []
    # Pausa não é falha: nada a reportar, e a sessão COM é liberada em vez de
    # ficar aberta ociosa enquanto ninguém coleta.
    assert destino.falhas == []
    assert provedor.aberto is False


def test_religar_volta_a_coletar_sem_esperar_o_intervalo_de_leitura() -> None:
    """Religar entra na impressão digital, então dispara leitura imediata."""
    pausada = _configuracao(paused=True)
    ativa = _configuracao()

    assert pausada.collection_fingerprint != ativa.collection_fingerprint


def test_leitura_e_configuracao_correm_em_relogios_independentes() -> None:
    origem = _OrigemFixa(_configuracao())
    destino = _DestinoEspiao()

    # Cinco leituras a cada 5s cobrem 25s -- ainda dentro do intervalo de
    # verificação de 30s. O intervalo menor não pode arrastar o maior para
    # um polling de configuração a cada ciclo.
    _rodar(origem, destino, _ProfitFalso(rodando=True), ciclos=5)

    assert len(destino.entregas) == 5
    assert origem.chamadas == 1
