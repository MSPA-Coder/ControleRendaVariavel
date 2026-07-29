from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from flask import current_app, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from app import db
from app.models import Broker, Position, PositionKind, Side, Ticker
from app.portfolio import build_portfolio
from app.routes import bp
from app.routes.helpers import (
    broker_records,
    brokers,
    poll_interval_seconds,
    positions_query,
    rtd_service,
    selected_filters,
    ticker_records,
)


@dataclass(frozen=True, slots=True)
class PositionInput:
    broker_id: int
    ticker_id: int
    quantity: Decimal
    average_cost: Decimal
    side: Side
    opened_on: date
    quote_multiplier: Decimal
    target_multiplier: Decimal
    result_mode: str
    position_kind: PositionKind


def _parse_form() -> PositionInput:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        broker_id = int(raw["broker_id"])
        ticker_id = int(raw["ticker_id"])
        quantity = Decimal(raw["quantity"])
        average_cost = Decimal(raw["average_cost"])
        quote_multiplier = Decimal(raw["quote_multiplier"])
        target_multiplier = Decimal(raw["target_multiplier"])
        opened_on = date.fromisoformat(raw["opened_on"])
        side = Side(raw["side"])
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("Há um valor ausente ou inválido no formulário.") from exc
    if db.session.get(Broker, broker_id) is None or db.session.get(Ticker, ticker_id) is None:
        raise ValueError("Selecione uma corretora e um ticker cadastrados.")
    if quantity <= 0 or average_cost < 0 or quote_multiplier <= 0 or target_multiplier <= 0:
        raise ValueError(
            "Quantidade e multiplicadores devem ser positivos; custo não pode ser negativo."
        )
    result_mode = raw.get("result_mode", "").upper()
    try:
        position_kind = PositionKind(raw.get("position_kind", PositionKind.REAL.value))
    except ValueError as exc:
        raise ValueError("Tipo de posição inválido.") from exc
    if result_mode not in {"L", "B"}:
        raise ValueError("Modo de resultado inválido.")
    return PositionInput(
        broker_id,
        ticker_id,
        quantity,
        average_cost,
        side,
        opened_on,
        quote_multiplier,
        target_multiplier,
        result_mode,
        position_kind,
    )


@bp.get("/")
def index() -> str:
    position_kind, broker, raw_kind = selected_filters()
    portfolio = build_portfolio(
        positions_query(position_kind, broker),
        stale_after_seconds=current_app.config["RTD_STALE_AFTER_SECONDS"],
    )
    service = rtd_service()
    try:
        rtd_service_running = service.is_running
        rtd_service_available = service.available
    except RuntimeError:
        rtd_service_running = False
        rtd_service_available = False
    return render_template(
        "index.html",
        portfolio=portfolio,
        brokers=brokers(),
        selected_broker=broker or "",
        selected_kind=raw_kind,
        poll_interval_seconds=poll_interval_seconds(),
        rtd_service_running=rtd_service_running,
        rtd_service_available=rtd_service_available,
    )


@bp.get("/positions/new")
def new_position() -> str:
    return render_template(
        "position_form.html",
        position=None,
        brokers=broker_records(),
        tickers=ticker_records(),
        sides=Side,
        position_kinds=PositionKind,
    )


@bp.post("/positions")
def create_position() -> ResponseReturnValue:
    try:
        data = _parse_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "position_form.html",
            position=request.form,
            brokers=broker_records(),
            tickers=ticker_records(),
            sides=Side,
            position_kinds=PositionKind,
        ), 422
    db.session.add(Position(**asdict(data)))
    db.session.commit()
    flash("Posição adicionada.", "success")
    return redirect(url_for("portfolio.index"))


@bp.get("/positions/<int:position_id>/edit")
def edit_position(position_id: int) -> str:
    position = db.get_or_404(Position, position_id)
    return render_template(
        "position_form.html",
        position=position,
        brokers=broker_records(),
        tickers=ticker_records(),
        sides=Side,
        position_kinds=PositionKind,
    )


@bp.post("/positions/<int:position_id>")
def update_position(position_id: int) -> ResponseReturnValue:
    position = db.get_or_404(Position, position_id)
    try:
        data = _parse_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "position_form.html",
            position=request.form,
            brokers=broker_records(),
            tickers=ticker_records(),
            sides=Side,
            position_kinds=PositionKind,
        ), 422
    for key, value in asdict(data).items():
        setattr(position, key, value)
    db.session.commit()
    flash("Posição atualizada.", "success")
    return redirect(url_for("portfolio.index"))


@bp.post("/positions/<int:position_id>/delete")
def delete_position(position_id: int) -> ResponseReturnValue:
    position = db.get_or_404(Position, position_id)
    db.session.delete(position)
    db.session.commit()
    flash("Posição excluída.", "success")
    return redirect(url_for("portfolio.index"))
