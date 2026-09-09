from __future__ import annotations

import logging
import socket
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_collector_loop import (
    ABERTA,
    _configuracao,
    _DestinoEspiao,
    _OrigemFixa,
    _OrigemQueFalha,
    _ProfitFalso,
    _rodar,
)

from app.collector import remote_agent
from app.collector.control import CollectorStop, event_name
from app.collector.database import (
    LocalDatabaseUnavailableError,
    ensure_local_database_reachable,
)
from app.collector.lock import CollectorAlreadyRunningError, collector_process_lock
from app.collector.loop import run_collector_loop
from app.collector.providers import CollectorProviderManager


def test_locks_permitem_local_e_remoto_mas_nao_duplicam_destino(tmp_path):
    with collector_process_lock(tmp_path), collector_process_lock(tmp_path, destination="remote"):
        for destination in ("local", "remote"):
            with (
                pytest.raises(CollectorAlreadyRunningError),
                collector_process_lock(tmp_path, destination=destination),
            ):
                pass


def test_parada_interrompe_espera_e_nao_afeta_outro_destino(tmp_path):
    with CollectorStop(tmp_path, "local") as local, CollectorStop(tmp_path, "remote") as remote:
        local.stop()
        assert local.wait(300) is True
        assert not local.running()
        assert remote.running()
    assert event_name(tmp_path, "local") != event_name(tmp_path, "remote")
    assert event_name(tmp_path, "local") != event_name(tmp_path / "outro", "local")


def test_processo_parado_nao_consulta_nem_abre_provedor():
    source = _OrigemFixa(_configuracao())
    providers = CollectorProviderManager(lambda _: pytest.fail("não deveria abrir COM"))
    run_collector_loop(
        source=source,
        sink=_DestinoEspiao(),
        providers=providers,
        detector=_ProfitFalso(rodando=True),
        logger=logging.getLogger("teste.stop"),
        initial_schedule=ABERTA,
        initial_check_interval=30,
        should_continue=lambda: False,
        sleep=lambda _: pytest.fail("não deve ficar esperando habilitação"),
    )
    assert source.chamadas == 0


def test_banco_local_indisponivel_encerra_sem_vigia_ou_retentativa():
    source = _OrigemQueFalha()
    sink = _DestinoEspiao()
    with pytest.raises(RuntimeError, match="coletor local encerrado"):
        run_collector_loop(
            source=source,
            sink=sink,
            providers=CollectorProviderManager(lambda _: pytest.fail("não deveria abrir COM")),
            detector=_ProfitFalso(rodando=True),
            logger=logging.getLogger("teste.local"),
            initial_schedule=ABERTA,
            initial_check_interval=30,
            stop_on_configuration_error=True,
            sleep=lambda _: pytest.fail("não deve aguardar banco voltar"),
        )
    assert source.chamadas == 1
    assert sink.falhas == []  # Não tenta gravar erro no banco indisponível.


def test_probe_do_banco_local_desiste_rapido_quando_a_porta_esta_fechada():
    # Uma porta efêmera reservada e fechada recusa na hora em qualquer
    # plataforma -- é o caso do contêiner parado, sem depender de o Windows
    # recusar o TCP como o Linux recusa.
    livre = socket.socket()
    livre.bind(("127.0.0.1", 0))
    host, port = livre.getsockname()
    livre.close()

    inicio = time.monotonic()
    with pytest.raises(LocalDatabaseUnavailableError, match="Banco local não respondeu"):
        ensure_local_database_reachable((host, port), timeout=3.0)
    assert time.monotonic() - inicio < 3.0


def test_probe_do_banco_local_passa_quando_ha_quem_aceite():
    ouvinte = socket.socket()
    ouvinte.bind(("127.0.0.1", 0))
    ouvinte.listen(1)
    try:
        ensure_local_database_reachable(ouvinte.getsockname(), timeout=3.0)
    finally:
        ouvinte.close()


def test_remoto_continua_tentando_no_intervalo_configurado():
    source = _OrigemQueFalha()
    sink = _DestinoEspiao()
    _rodar(source, sink, _ProfitFalso(rodando=True), ciclos=3)
    assert source.chamadas == 3
    assert len(sink.falhas) == 3


def test_producao_nao_le_env_local_nem_cria_flask(monkeypatch, tmp_path):
    import app

    read = []
    monkeypatch.setattr(remote_agent, "_read_dotenv", lambda path: read.append(path) or {})
    assert remote_agent._environment(tmp_path) == {}
    assert read == [tmp_path / remote_agent.CONFIG_PATH]
    monkeypatch.setattr(app, "create_app", lambda *a, **k: pytest.fail("Flask não deve iniciar"))
    monkeypatch.setattr(remote_agent, "remote_loop_arguments", lambda _: {})
    calls = []
    monkeypatch.setattr(remote_agent, "run_collector_loop", lambda **kwargs: calls.append(kwargs))
    remote_agent.run(tmp_path)
    assert len(calls) == 1
    assert calls[0]["should_continue"] is not None


def test_configuracao_inalterada_nao_regrava_estado(monkeypatch, tmp_path):
    monkeypatch.setattr(
        remote_agent, "_environment", lambda _: {"COLLECTOR_REMOTE_URL": "https://example.test"}
    )
    monkeypatch.setattr(remote_agent, "_read_token", lambda *a: "x" * 32)
    monkeypatch.setattr(remote_agent, "_state_path", lambda _: tmp_path / "state.json")
    monkeypatch.setattr(remote_agent, "_load_collector_schedule", lambda _: ABERTA)
    monkeypatch.setattr(remote_agent, "_load_agent_check_interval", lambda _: 30)
    writes = []
    monkeypatch.setattr(remote_agent, "_store_agent_state", lambda *a: writes.append(a))
    callback = remote_agent.remote_loop_arguments(tmp_path)["on_configuration"]
    config = _configuracao()
    for _ in range(10):
        callback(config)
    assert writes == []
    changed = replace(config, agent_check_interval_seconds=60)
    callback(changed)
    callback(changed)
    assert len(writes) == 1


def test_entrypoint_remoto_resolve_raiz_do_projeto(monkeypatch):
    calls = []
    monkeypatch.setattr(remote_agent, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(remote_agent, "run", lambda project: calls.append(project))
    remote_agent.main()
    assert calls == [Path(remote_agent.__file__).resolve().parents[2]]
