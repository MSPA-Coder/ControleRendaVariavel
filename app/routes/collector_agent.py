"""API privada usada pelo agente RTD do Windows.

O endpoint é propositalmente pequeno: o agente somente recebe os instrumentos
abertos e devolve leituras normalizadas. Ele não recebe acesso ao PostgreSQL,
nem o VPS inicia conexões para o computador Windows.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from flask import abort, current_app, jsonify, request

from app import db
from app.collector.database import (
    OptionReading,
    load_collector_positions,
    option_instrument_keys,
    persist_readings,
    record_agent_online,
)
from app.collector.rtd import QuoteValue, parse_decimal
from app.collector.settings import schedule_from_settings
from app.models import AppSetting
from app.routes import bp

MAX_BODY_BYTES = 512 * 1024


def _settings() -> AppSetting:
    settings = db.session.get(AppSetting, 1)
    if settings is None:
        from app.collector.settings import default_collector_settings

        settings = default_collector_settings()
        db.session.add(settings)
        db.session.flush()
    return settings


def _require_agent_token() -> None:
    configured = str(current_app.config["COLLECTOR_AGENT_TOKEN"])
    supplied = request.headers.get("Authorization", "")
    if not configured:
        abort(503, "Coletor remoto não configurado.")
    if not hmac.compare_digest(supplied, f"Bearer {configured}"):
        abort(401, "Não autorizado.")


def _record_agent_seen(settings: AppSetting) -> None:
    now = datetime.now(UTC)
    if settings.collector_agent_seen_at is None or (
        now - settings.collector_agent_seen_at
    ).total_seconds() >= 30:
        settings.collector_agent_seen_at = now
        if settings.collector_agent_status != "error":
            settings.collector_agent_status = "waiting"
        db.session.commit()


def _as_aware_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("observed_at ausente")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observed_at sem fuso horário")
    return parsed.astimezone(UTC)


def _decimal(payload: dict[str, Any], field: str) -> Decimal:
    return parse_decimal(payload.get(field))


def _string(payload: dict[str, Any], field: str, maximum: int = 16) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} ausente")
    return value.strip()[:maximum]


def _json_body() -> dict[str, Any]:
    if request.content_length is None or request.content_length > MAX_BODY_BYTES:
        abort(413, "Carga inválida.")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, "JSON inválido.")
    return payload


@bp.get("/api/collector/configuration")
def collector_agent_configuration():
    _require_agent_token()
    settings = _settings()
    _record_agent_seen(settings)
    positions, option_positions = load_collector_positions()
    return jsonify(
        collector_mode=settings.collector_mode.value,
        poll_interval_seconds=settings.poll_interval_seconds,
        agent_check_interval_seconds=settings.agent_check_interval_seconds,
        collector_schedule=schedule_from_settings(settings).as_payload(),
        refresh_requested=settings.collector_refresh_requested_at is not None,
        paused=settings.collector_paused,
        positions=[
            {
                "position_id": item.id,
                "ticker": item.ticker_ref.symbol,
                "market_code": item.ticker_ref.rtd_market_code,
                "side": item.side.value,
            }
            for item in positions
        ],
        option_positions=[
            {
                "option_position_id": item.id,
                "ticker": item.contract.ticker_ref.symbol,
                "market_code": item.contract.ticker_ref.rtd_market_code,
                "underlying_ticker": item.contract.underlying_ticker_ref.symbol,
                "underlying_market_code": item.contract.underlying_ticker_ref.rtd_market_code,
            }
            for item in option_positions
        ],
    )


def _stock_reading(item: object) -> QuoteValue:
    if not isinstance(item, dict):
        raise ValueError("cotação inválida")
    return QuoteValue(
        position_id=int(item["position_id"]),
        last_price=_decimal(item, "last_price"),
        previous_close=_decimal(item, "previous_close"),
        instrument_status=_string(item, "instrument_status"),
        observed_at=_as_aware_datetime(item.get("observed_at")),
        last_trade_price=_decimal(item, "history_price"),
    )


def _option_reading(item: object) -> OptionReading:
    if not isinstance(item, dict):
        raise ValueError("cotação inválida")
    observed_at = _as_aware_datetime(item.get("observed_at"))
    option_position_id = int(item["option_position_id"])
    return OptionReading(
        option_position_id=option_position_id,
        option=QuoteValue(
            # A mesma chave sintética que o coletor usa para a opção, para o
            # valor continuar identificável se ele voltar a circular solto.
            position_id=option_instrument_keys(option_position_id)[0],
            last_price=_decimal(item, "last_price"),
            previous_close=_decimal(item, "previous_close"),
            instrument_status=_string(item, "instrument_status"),
            observed_at=observed_at,
            last_trade_price=_decimal(item, "history_price"),
        ),
        underlying_last_price=_decimal(item, "underlying_price"),
        underlying_history_price=_decimal(item, "underlying_history_price"),
    )


@bp.post("/api/collector/quotes")
def collector_agent_quotes():
    _require_agent_token()
    payload = _json_body()
    positions_payload = payload.get("positions", [])
    options_payload = payload.get("option_positions", [])
    if not isinstance(positions_payload, list) or not isinstance(options_payload, list):
        abort(400, "Formato de cotações inválido.")
    if len(positions_payload) + len(options_payload) > 2_000:
        abort(400, "Quantidade de cotações inválida.")
    try:
        # A escrita é a mesma do coletor local (app/collector_database.py); o
        # que este endpoint acrescenta é desconfiar de cada campo antes.
        persist_readings(
            [_stock_reading(item) for item in positions_payload],
            [_option_reading(item) for item in options_payload],
        )
    except (KeyError, TypeError, ValueError) as exc:
        db.session.rollback()
        abort(400, str(exc))
    record_agent_online(_settings())
    db.session.commit()
    return jsonify(updated=len(positions_payload) + len(options_payload))


@bp.post("/api/collector/failure")
def collector_agent_failure():
    _require_agent_token()
    payload = _json_body()
    error = payload.get("error")
    if not isinstance(error, str) or not error.strip():
        abort(400, "Erro inválido.")
    settings = _settings()
    settings.collector_agent_seen_at = datetime.now(UTC)
    settings.collector_agent_status = "error"
    settings.collector_agent_error = error.strip()[:250]
    db.session.commit()
    return jsonify(recorded=True)
