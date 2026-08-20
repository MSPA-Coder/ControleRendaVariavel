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
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import joinedload

from app import db
from app.domain import MARKET_TIMEZONE
from app.models import AppSetting, OptionContract, OptionPosition, OptionQuote, Position, Quote
from app.routes import bp
from app.routes.helpers import upsert_quote_history
from app.rtd import parse_decimal

MAX_BODY_BYTES = 512 * 1024


def _settings() -> AppSetting:
    settings = db.session.get(AppSetting, 1)
    if settings is None:
        from app.collector_settings import default_collector_settings

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
    positions = list(
        db.session.scalars(
            select(Position)
            .options(joinedload(Position.ticker_ref))
            .order_by(Position.id)
        )
    )
    option_positions = list(
        db.session.scalars(
            select(OptionPosition)
            .options(
                joinedload(OptionPosition.contract).joinedload(OptionContract.ticker_ref),
                joinedload(OptionPosition.contract).joinedload(OptionContract.underlying_ticker_ref),
            )
            .order_by(OptionPosition.id)
        ).unique()
    )
    return jsonify(
        collector_mode=settings.collector_mode.value,
        poll_interval_seconds=settings.poll_interval_seconds,
        agent_check_interval_seconds=settings.agent_check_interval_seconds,
        collector_schedule={
            "weekdays": [
                int(value)
                for value in settings.collector_schedule_weekdays.split(",")
                if value.isdigit()
            ],
            "start_time": settings.collector_schedule_start_time.strftime("%H:%M"),
            "end_time": settings.collector_schedule_end_time.strftime("%H:%M"),
        },
        refresh_requested=settings.collector_refresh_requested_at is not None,
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
        position_ids = {int(item["position_id"]) for item in positions_payload if isinstance(item, dict)}
        option_position_ids = {
            int(item["option_position_id"]) for item in options_payload if isinstance(item, dict)
        }
        positions = {
            item.id: item
            for item in db.session.scalars(
                select(Position).where(Position.id.in_(position_ids))
            )
        }
        option_positions = {
            item.id: item
            for item in db.session.scalars(
                select(OptionPosition)
                .options(joinedload(OptionPosition.contract))
                .where(OptionPosition.id.in_(option_position_ids))
            ).unique()
        }
        if len(positions) != len(position_ids) or len(option_positions) != len(option_position_ids):
            raise ValueError("posição inexistente")
        ticker_prices: dict[int, tuple[Decimal, datetime]] = {}
        for item in positions_payload:
            if not isinstance(item, dict):
                raise ValueError("cotação inválida")
            position_id = int(item["position_id"])
            observed_at = _as_aware_datetime(item.get("observed_at"))
            statement = insert(Quote).values(
                position_id=position_id,
                last_price=_decimal(item, "last_price"),
                previous_close=_decimal(item, "previous_close"),
                instrument_status=_string(item, "instrument_status"),
                source_status="online",
                error_message=None,
                observed_at=observed_at,
            )
            db.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[Quote.position_id],
                    set_={
                        "last_price": statement.excluded.last_price,
                        "previous_close": statement.excluded.previous_close,
                        "instrument_status": statement.excluded.instrument_status,
                        "source_status": "online",
                        "error_message": None,
                        "observed_at": statement.excluded.observed_at,
                    },
                )
            )
            ticker_prices[positions[position_id].ticker_id] = (
                _decimal(item, "history_price"),
                observed_at,
            )
        for item in options_payload:
            if not isinstance(item, dict):
                raise ValueError("cotação inválida")
            option_position_id = int(item["option_position_id"])
            observed_at = _as_aware_datetime(item.get("observed_at"))
            statement = insert(OptionQuote).values(
                option_position_id=option_position_id,
                last_price=_decimal(item, "last_price"),
                previous_close=_decimal(item, "previous_close"),
                underlying_price=_decimal(item, "underlying_price"),
                instrument_status=_string(item, "instrument_status"),
                source_status="online",
                error_message=None,
                observed_at=observed_at,
            )
            db.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[OptionQuote.option_position_id],
                    set_={
                        "last_price": statement.excluded.last_price,
                        "previous_close": statement.excluded.previous_close,
                        "underlying_price": statement.excluded.underlying_price,
                        "instrument_status": statement.excluded.instrument_status,
                        "source_status": "online",
                        "error_message": None,
                        "observed_at": statement.excluded.observed_at,
                    },
                )
            )
            contract = option_positions[option_position_id].contract
            ticker_prices[contract.ticker_id] = (_decimal(item, "history_price"), observed_at)
            ticker_prices[contract.underlying_ticker_id] = (
                _decimal(item, "underlying_history_price"),
                observed_at,
            )
        upsert_quote_history(
            (
                ticker_id,
                price,
                observed_at.astimezone(MARKET_TIMEZONE).date(),
                observed_at,
            )
            for ticker_id, (price, observed_at) in ticker_prices.items()
        )
    except (KeyError, TypeError, ValueError) as exc:
        db.session.rollback()
        abort(400, str(exc))
    settings = _settings()
    settings.collector_agent_seen_at = datetime.now(UTC)
    settings.collector_agent_status = "online"
    settings.collector_agent_error = None
    settings.collector_refresh_requested_at = None
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
