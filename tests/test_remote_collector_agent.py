from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal

from app.remote_collector_agent import (
    DEFAULT_AGENT_CHECK_INTERVAL_SECONDS,
    AgentDeadlines,
    CollectorSchedule,
    _instrument_sets,
    _load_agent_check_interval,
    _load_collector_schedule,
    _quotes_payload,
    _schedule_from_payload,
    _store_agent_check_interval,
    _store_agent_state,
    _valid_agent_check_interval,
    _valid_poll_interval,
)
from app.rtd import QuoteValue


def test_agente_monta_instrumentos_e_payload_sem_expor_acesso_ao_banco() -> None:
    instruments, option_keys = _instrument_sets(
        {
            "positions": [
                {"position_id": 7, "ticker": "ABCD3", "market_code": "B", "side": "C"}
            ],
            "option_positions": [
                {
                    "option_position_id": 3,
                    "ticker": "ABCDH100",
                    "market_code": "B",
                    "underlying_ticker": "ABCD3",
                    "underlying_market_code": "B",
                }
            ],
        }
    )

    assert [item.position_id for item in instruments] == [7, -6, -7]
    values = [
        QuoteValue(7, Decimal("10"), Decimal("9"), "A", datetime.now(UTC)),
        QuoteValue(-6, Decimal("2"), Decimal("1"), "A", datetime.now(UTC)),
        QuoteValue(-7, Decimal("10"), Decimal("9"), "A", datetime.now(UTC)),
    ]

    payload = _quotes_payload(values, option_keys)

    assert payload["positions"][0]["position_id"] == 7
    assert payload["option_positions"][0]["option_position_id"] == 3
    assert payload["option_positions"][0]["underlying_price"] == "10"


def test_api_do_agente_recusa_chamada_sem_token(client) -> None:
    client.application.config["COLLECTOR_AGENT_TOKEN"] = "a" * 32
    response = client.get("/api/collector/configuration")

    assert response.status_code == 401


def test_agente_guarda_e_reaproveita_intervalo_no_arquivo_local(tmp_path) -> None:
    state_path = tmp_path / "remote-collector-state.json"

    assert _load_agent_check_interval(state_path) == DEFAULT_AGENT_CHECK_INTERVAL_SECONDS
    _store_agent_check_interval(state_path, 30)

    assert _load_agent_check_interval(state_path) == 30
    assert _valid_agent_check_interval(5) == 5


def test_agente_rejeita_intervalo_de_verificacao_fora_da_faixa() -> None:
    import pytest

    with pytest.raises(ValueError):
        _valid_agent_check_interval(4)


def test_agente_mantem_leitura_e_configuracao_em_relogios_independentes() -> None:
    deadlines = AgentDeadlines()

    deadlines.schedule_configuration(0, 10)
    deadlines.schedule_quote(0, 60)

    assert deadlines.configuration_due(10) is True
    assert deadlines.quote_due(10) is False
    assert deadlines.sleep_seconds(10) == 0
    assert deadlines.quote_due(60) is True


def test_agente_valida_intervalo_de_leitura_recebido_do_servidor() -> None:
    import pytest

    assert _valid_poll_interval(60) == 60
    with pytest.raises(ValueError):
        _valid_poll_interval(0)


def test_agente_respeita_agenda_unica_para_b3_e_ativos_americanos() -> None:
    schedule = CollectorSchedule(frozenset({0, 1, 2, 3, 4}), time(9, 45), time(18, 10))

    assert schedule.is_active(datetime(2026, 8, 17, 12, 45, tzinfo=UTC)) is True
    assert schedule.is_active(datetime(2026, 8, 17, 21, 10, tzinfo=UTC)) is False
    assert schedule.is_active(datetime(2026, 8, 22, 15, 0, tzinfo=UTC)) is False


def test_agente_guarda_agenda_no_estado_local(tmp_path) -> None:
    state_path = tmp_path / "remote-collector-state.json"
    schedule = _schedule_from_payload(
        {"weekdays": [0, 1, 2, 3, 4], "start_time": "09:45", "end_time": "18:10"}
    )

    _store_agent_state(state_path, 30, schedule)

    assert _load_collector_schedule(state_path) == schedule
