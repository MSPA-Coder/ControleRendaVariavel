"""O provedor RTD direto, exercitado sem COM.

É o único caminho de leitura de cotação agora que o Excel saiu. Estes casos
cobrem as partes que quebram em silêncio: a decodificação do SAFEARRAY que o
`IRtdServer` devolve, a escolha entre preço de livro e último negócio, e o
laço que espera o primeiro snapshot em vez de publicar campos incompletos.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.collector import rtd_direct
from app.collector.rtd import Instrument
from app.collector.rtd_direct import (
    DirectRtdQuoteProvider,
    RtdUpdateEvent,
    decode_refresh_data,
    quote_fields,
)


class _FakeServer:
    """Faz o papel do adaptador do `IRtdServer` -- topics por id, refresh em lotes."""

    def __init__(
        self,
        connect_values: dict[tuple[str, str], object],
        refresh_batches: list[dict[int, object]],
    ) -> None:
        self._connect_values = connect_values
        self._refresh_batches = list(refresh_batches)
        self.topics: dict[int, tuple[str, str]] = {}
        self.started = False
        self.terminated = False
        self.disconnected: list[int] = []

    def ServerStart(self, _callback: object) -> int:
        self.started = True
        return 1

    def ConnectData(self, topic_id: int, strings: tuple[str, str], _get_new: bool) -> object:
        self.topics[topic_id] = tuple(strings)
        return self._connect_values.get(tuple(strings))

    def RefreshData(self, _topic_count: int) -> tuple[list[int], list[object]]:
        batch = self._refresh_batches.pop(0) if self._refresh_batches else {}
        return list(batch.keys()), list(batch.values())

    def DisconnectData(self, topic_id: int) -> None:
        self.disconnected.append(topic_id)

    def ServerTerminate(self) -> None:
        self.terminated = True


def _provider(server: _FakeServer, *, pump=lambda: None, **kwargs) -> DirectRtdQuoteProvider:
    return DirectRtdQuoteProvider(
        prog_id="fake",
        server_factory=lambda _prog_id: server,
        callback_wrapper=lambda callback: callback,
        pump_messages=pump,
        **kwargs,
    )


# --- decode_refresh_data --------------------------------------------------------


def test_decode_aceita_o_par_direto_de_ids_e_valores() -> None:
    assert decode_refresh_data(([1, 2], ["10", "20"])) == {1: "10", 2: "20"}


def test_decode_desembrulha_o_par_com_contagem_na_frente() -> None:
    # A forma `(payload, contagem)` que o adaptador do comtypes devolve.
    assert decode_refresh_data((([5], ["7"]), 1)) == {5: "7"}


@pytest.mark.parametrize("ruim", ["texto", b"bytes", [1, 2, 3], ([1], "ab"), 42])
def test_decode_devolve_vazio_para_forma_desconhecida(ruim: object) -> None:
    assert decode_refresh_data(ruim) == {}


# --- _build_quotes ------------------------------------------------------------


def test_build_recusa_enquanto_faltar_campo() -> None:
    inst = Instrument(1, "ABCD3", "B")
    topics = {1: (inst, "ULT"), 2: (inst, "FEC")}  # falta EST
    with pytest.raises(ValueError, match="campos RTD ainda incompletos"):
        DirectRtdQuoteProvider._build_quotes([inst], topics, {1: "10", 2: "9"})


def test_build_usa_preco_de_livro_com_status_aberto_e_ult_fora_dele() -> None:
    inst = Instrument(1, "ABCDT100", "B", "C")  # compra em B -> book_field "OCP"
    assert quote_fields(inst) == ("ULT", "FEC", "EST", "OCP")
    topics = {1: (inst, "ULT"), 2: (inst, "FEC"), 3: (inst, "EST"), 4: (inst, "OCP")}

    aberto = DirectRtdQuoteProvider._build_quotes(
        [inst], topics, {1: "10", 2: "9", 3: "A", 4: "10.5"}
    )[0]
    assert aberto.last_price == Decimal("10.5")  # preço de livro (OCP)
    assert aberto.last_trade_price == Decimal("10")  # ULT segue para o histórico
    assert aberto.quote_history_price == Decimal("10")

    fechado = DirectRtdQuoteProvider._build_quotes(
        [inst], topics, {1: "10", 2: "9", 3: "F", 4: "10.5"}
    )[0]
    assert fechado.last_price == Decimal("10")  # fora de "A"/"L" volta para ULT


# --- fetch ponta a ponta -----------------------------------------------------


def test_fetch_espera_o_primeiro_snapshot_antes_de_devolver() -> None:
    inst = Instrument(1, "ABCD3", "B")
    server = _FakeServer(
        connect_values={("ABCD3_B_0", "ULT"): "10", ("ABCD3_B_0", "EST"): "A"},
        refresh_batches=[{2: "9"}],  # FEC só chega no primeiro RefreshData
    )
    with _provider(server) as provider:
        values = provider.fetch([inst])

    assert [(v.position_id, v.last_price, v.previous_close) for v in values] == [
        (1, Decimal("10"), Decimal("9"))
    ]
    assert server.terminated is True


def test_fetch_propaga_desconexao_do_servidor(monkeypatch) -> None:
    inst = Instrument(1, "ABCD3", "B")
    server = _FakeServer(connect_values={}, refresh_batches=[])

    def pump() -> None:
        provider._callback.disconnected = True

    provider = _provider(server, pump=pump)
    with pytest.raises(ConnectionError, match="encerrou a conexão"), provider:
        provider.fetch([inst])


def test_fetch_estoura_prazo_com_diagnostico(monkeypatch) -> None:
    relogio = {"agora": 0.0}
    monkeypatch.setattr(rtd_direct.time, "monotonic", lambda: relogio["agora"])
    monkeypatch.setattr(
        rtd_direct.time, "sleep", lambda s: relogio.__setitem__("agora", relogio["agora"] + s)
    )
    inst = Instrument(1, "ABCD3", "B")
    server = _FakeServer(connect_values={}, refresh_batches=[])
    provider = _provider(server, timeout_seconds=1, refresh_seconds=0.25)
    with provider, pytest.raises(TimeoutError, match="RTD direto não respondeu em 1s"):
        provider.fetch([inst])


def test_rtd_update_event_conta_notificacoes() -> None:
    callback = RtdUpdateEvent()
    callback.UpdateNotify()
    callback.UpdateNotify()
    assert callback.update_pending is True
    assert callback.update_count == 2
