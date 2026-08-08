from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from app import db
from app.models import Broker, Position, PositionKind, Side, Ticker
from app.portfolio import PortfolioView, build_portfolio
from app.position_closure import (
    close_open_position,
    create_open_transaction_for_position,
    delete_open_transaction_for_position,
    sync_open_transaction_for_position,
)
from app.routes import bp
from app.routes.helpers import (
    allocation_chart_data,
    broker_exposure_chart_data,
    broker_records,
    brokers,
    investable_ticker_records,
    market_exposure_chart_data,
    poll_interval_seconds,
    positions_query,
    quote_stale_after_seconds,
    rtd_service,
    selected_filters,
)
from app.validation import parse_finite_decimal


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
        quantity = parse_finite_decimal(raw["quantity"], field_name="uma quantidade")
        average_cost = parse_finite_decimal(raw["average_cost"], field_name="um custo médio")
        quote_multiplier = parse_finite_decimal(
            raw["quote_multiplier"], field_name="um multiplicador de cotação"
        )
        target_multiplier = parse_finite_decimal(
            raw["target_multiplier"], field_name="um multiplicador de target"
        )
        opened_on = date.fromisoformat(raw["opened_on"])
        side = Side(raw["side"])
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("Há um valor ausente ou inválido no formulário.") from exc
    ticker = db.session.get(Ticker, ticker_id)
    if db.session.get(Broker, broker_id) is None or ticker is None:
        raise ValueError("Selecione uma corretora e um ticker cadastrados.")
    if ticker.is_benchmark:
        raise ValueError(
            "Esse ticker está marcado como referência de comparação e não pode "
            "ter posição própria."
        )
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
    group_by_broker = request.args.get("group_by_broker") == "1"
    portfolio = build_portfolio(
        positions_query(position_kind, broker, group_by_broker=group_by_broker),
        stale_after_seconds=quote_stale_after_seconds(),
    )
    service = rtd_service()
    try:
        rtd_service_running = service.is_running
        rtd_service_available = service.available
        rtd_service_status = service.status
    except (OSError, RuntimeError):
        rtd_service_running = False
        rtd_service_available = False
        rtd_service_status = "unavailable"
    return render_template(
        "index.html",
        portfolio=portfolio,
        brokers=brokers(),
        selected_broker=broker or "",
        selected_kind=raw_kind,
        group_by_broker=group_by_broker,
        poll_interval_seconds=poll_interval_seconds(),
        rtd_service_running=rtd_service_running,
        rtd_service_available=rtd_service_available,
        rtd_service_status=rtd_service_status,
    )


@bp.get("/positions/new")
def new_position() -> str:
    return render_template(
        "position_form.html",
        position=None,
        brokers=broker_records(),
        tickers=investable_ticker_records(),
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
            tickers=investable_ticker_records(),
            sides=Side,
            position_kinds=PositionKind,
        ), 422
    position = Position(**asdict(data))
    db.session.add(position)
    db.session.flush()
    create_open_transaction_for_position(position)
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
        tickers=investable_ticker_records(),
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
            tickers=investable_ticker_records(),
            sides=Side,
            position_kinds=PositionKind,
        ), 422
    for key, value in asdict(data).items():
        setattr(position, key, value)
    sync_open_transaction_for_position(position)
    db.session.commit()
    flash("Posição atualizada.", "success")
    return redirect(url_for("portfolio.index"))


@bp.post("/positions/<int:position_id>/delete")
def delete_position(position_id: int) -> ResponseReturnValue:
    position = db.get_or_404(Position, position_id)
    delete_open_transaction_for_position(position.id)
    db.session.delete(position)
    db.session.commit()
    flash("Posição excluída.", "success")
    return redirect(url_for("portfolio.index"))


@bp.get("/positions/<int:position_id>/close")
def close_position_form(position_id: int) -> str:
    position = db.get_or_404(Position, position_id)
    default_price = position.quote.last_price if position.quote else position.average_cost
    return render_template(
        "close_position_form.html",
        position=position,
        default_price=default_price,
        default_date=date.today().isoformat(),
    )


@bp.post("/positions/<int:position_id>/close")
def close_position(position_id: int) -> ResponseReturnValue:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        exit_price = parse_finite_decimal(raw["exit_price"], field_name="um preço de saída")
        closed_on = date.fromisoformat(raw["closed_on"])
    except (KeyError, ValueError, ArithmeticError):
        flash("Informe um preço de saída e uma data válidos.", "error")
        return redirect(url_for("portfolio.close_position_form", position_id=position_id))
    if exit_price < 0:
        flash("O preço de saída não pode ser negativo.", "error")
        return redirect(url_for("portfolio.close_position_form", position_id=position_id))
    try:
        transaction = close_open_position(position_id, exit_price, closed_on)
    except ValueError:
        flash("A data de encerramento não pode ser anterior à data de abertura.", "error")
        return redirect(url_for("portfolio.close_position_form", position_id=position_id))
    if transaction is None:
        flash("A posição já foi encerrada ou não existe.", "error")
        return redirect(url_for("portfolio.transactions"))
    flash("Posição encerrada e registrada em Transações.", "success")
    return redirect(url_for("portfolio.transactions"))


def _render_exposure(
    template: str, chart_data: Callable[[PortfolioView], list[dict[str, object]]]
) -> str:
    """Renderiza uma das páginas de Análise > Exposição.

    As três páginas só diferem no template e em qual recorte da carteira
    alimenta o gráfico; os filtros, a consulta e o contexto são idênticos.
    """
    position_kind, broker, raw_kind = selected_filters()
    portfolio = build_portfolio(
        positions_query(position_kind, broker),
        stale_after_seconds=quote_stale_after_seconds(),
    )
    return render_template(
        template,
        portfolio=portfolio,
        brokers=brokers(),
        selected_broker=broker or "",
        selected_kind=raw_kind,
        allocation_charts=chart_data(portfolio),
    )


@bp.get("/analysis/exposure-asset")
def exposure_asset() -> str:
    return _render_exposure(
        "exposure_asset.html", lambda portfolio: allocation_chart_data(portfolio.positions)
    )


@bp.get("/analysis/exposure-broker")
def exposure_broker() -> str:
    return _render_exposure(
        "exposure_broker.html",
        lambda portfolio: broker_exposure_chart_data(portfolio.broker_groups),
    )


@bp.get("/analysis/exposure-market")
def exposure_market() -> str:
    return _render_exposure(
        "exposure_market.html",
        lambda portfolio: market_exposure_chart_data(portfolio.market_groups),
    )
