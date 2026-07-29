from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import cast

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app import db
from app.collector_settings import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    MAX_POLL_INTERVAL_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    default_collector_settings,
    parse_collector_settings,
)
from app.models import (
    AppSetting,
    Broker,
    CollectorMode,
    Market,
    OptionContract,
    OptionExpiration,
    OptionType,
    Position,
    PositionKind,
    Side,
    Ticker,
)
from app.portfolio import build_portfolio
from app.reference_data import parse_broker, parse_ticker
from app.rtd_service import RtdService

bp = Blueprint("portfolio", __name__)


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


def _positions(
    position_kind: PositionKind | None = None, broker: str | None = None
) -> list[Position]:
    statement = (
        select(Position)
        .join(Position.broker_ref)
        .join(Position.ticker_ref)
        .options(
            joinedload(Position.quote),
            joinedload(Position.broker_ref),
            joinedload(Position.ticker_ref),
        )
        .order_by(Ticker.currency, Broker.name, Ticker.symbol, Position.opened_on)
    )
    if position_kind is not None:
        statement = statement.where(Position.position_kind == position_kind)
    if broker:
        statement = statement.where(Broker.name == broker)
    return list(db.session.scalars(statement).unique())


def _brokers() -> list[str]:
    statement = select(Broker.name).order_by(Broker.name)
    return list(db.session.scalars(statement))


def _broker_records() -> list[Broker]:
    return list(db.session.scalars(select(Broker).order_by(Broker.name)))


def _ticker_records() -> list[Ticker]:
    return list(db.session.scalars(select(Ticker).order_by(Ticker.symbol)))


def _option_expirations() -> list[OptionExpiration]:
    return list(
        db.session.scalars(
            select(OptionExpiration).order_by(OptionExpiration.exercise_date)
        )
    )


def _option_contracts() -> list[OptionContract]:
    statement = (
        select(OptionContract)
        .join(OptionContract.expiration)
        .options(
            joinedload(OptionContract.ticker_ref),
            joinedload(OptionContract.underlying_ticker_ref),
            joinedload(OptionContract.expiration),
        )
        .order_by(OptionExpiration.exercise_date, OptionContract.id)
    )
    return list(db.session.scalars(statement).unique())


def _poll_interval_seconds() -> int:
    settings = db.session.get(AppSetting, 1)
    if settings is None:
        return DEFAULT_POLL_INTERVAL_SECONDS
    return settings.poll_interval_seconds


def _rtd_service() -> RtdService:
    return cast(RtdService, current_app.extensions["rtd_service"])


def _selected_filters() -> tuple[PositionKind | None, str | None, str]:
    raw_kind = request.args.get("position_kind", PositionKind.REAL.value)
    try:
        kind = None if raw_kind == "all" else PositionKind(raw_kind)
    except ValueError:
        kind, raw_kind = PositionKind.REAL, PositionKind.REAL.value
    broker = request.args.get("broker") or None
    return kind, broker, raw_kind


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
    position_kind, broker, raw_kind = _selected_filters()
    portfolio = build_portfolio(
        _positions(position_kind, broker),
        stale_after_seconds=current_app.config["RTD_STALE_AFTER_SECONDS"],
    )
    service = _rtd_service()
    try:
        rtd_service_running = service.is_running
        rtd_service_available = service.available
    except RuntimeError:
        rtd_service_running = False
        rtd_service_available = False
    return render_template(
        "index.html",
        portfolio=portfolio,
        brokers=_brokers(),
        selected_broker=broker or "",
        selected_kind=raw_kind,
        poll_interval_seconds=_poll_interval_seconds(),
        rtd_service_running=rtd_service_running,
        rtd_service_available=rtd_service_available,
    )


@bp.route("/api/rtd-service", methods=["GET", "POST"])
def rtd_service_api() -> ResponseReturnValue:
    service = _rtd_service()
    try:
        if request.method == "POST":
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("enabled"), bool
            ):
                return jsonify(error="Informe o estado booleano 'enabled'."), 400
            if payload["enabled"]:
                service.start()
            else:
                service.stop()
        return jsonify(running=service.is_running, available=service.available)
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Não foi possível acessar o coletor RTD: %s", exc)
        return jsonify(error=str(exc), running=False, available=False), 503


@bp.get("/api/portfolio")
def portfolio_api() -> ResponseReturnValue:
    position_kind, broker, _raw_kind = _selected_filters()
    portfolio = build_portfolio(
        _positions(position_kind, broker),
        stale_after_seconds=current_app.config["RTD_STALE_AFTER_SECONDS"],
    )
    rows = []
    for view in portfolio.positions:
        metric = view.metrics
        rows.append(
            {
                "id": view.position.id,
                "ticker": view.position.ticker,
                "broker": view.position.broker,
                "position_kind": view.position.position_kind.value,
                "quote_status": view.quote_status,
                "current_price": str(metric.current_price) if metric else None,
                "daily_variation": str(metric.daily_variation) if metric else None,
                "result": str(metric.result) if metric else None,
                "return_pct": str(metric.return_pct)
                if metric and metric.return_pct is not None
                else None,
                "annualized_return": (
                    str(metric.annualized_return)
                    if metric and metric.annualized_return is not None
                    else None
                ),
                "current_weight": str(view.current_weight)
                if view.current_weight is not None
                else None,
                "cost_weight": str(view.cost_weight) if view.cost_weight is not None else None,
            }
        )
    return jsonify(
        rows=rows,
        totals=[
            {
                "currency": total.currency,
                "current": str(total.current_total),
                "cost": str(total.cost_total),
                "result": str(total.result_total),
            }
            for total in portfolio.currency_totals
        ],
        brokers=[
            {
                "broker": group.broker,
                "currency": group.currency,
                "current": str(group.current_total),
                "cost": str(group.cost_total),
                "result": str(group.result_total),
            }
            for group in portfolio.broker_groups
        ],
        poll_interval_seconds=_poll_interval_seconds(),
    )


@bp.route("/settings", methods=["GET", "POST"])
def settings() -> ResponseReturnValue:
    current_settings = db.session.get(AppSetting, 1)
    if current_settings is None:
        current_settings = default_collector_settings()
        db.session.add(current_settings)
        db.session.flush()

    if request.method == "POST":
        try:
            data = parse_collector_settings(request.form)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            submitted = default_collector_settings()
            raw_mode = request.form.get("collector_mode", "")
            if raw_mode in {mode.value for mode in CollectorMode}:
                submitted.collector_mode = CollectorMode(raw_mode)
            try:
                submitted.poll_interval_seconds = int(
                    request.form.get("poll_interval_seconds", "2")
                )
            except ValueError:
                submitted.poll_interval_seconds = 2
            return render_template(
                "settings.html",
                settings=submitted,
                min_interval=MIN_POLL_INTERVAL_SECONDS,
                max_interval=MAX_POLL_INTERVAL_SECONDS,
            ), 422
        current_settings.collector_mode = data.collector_mode
        current_settings.poll_interval_seconds = data.poll_interval_seconds
        db.session.commit()
        flash("Configurações do coletor atualizadas.", "success")
        return redirect(url_for("portfolio.settings"))

    db.session.commit()
    return render_template(
        "settings.html",
        settings=current_settings,
        min_interval=MIN_POLL_INTERVAL_SECONDS,
        max_interval=MAX_POLL_INTERVAL_SECONDS,
    )


@bp.get("/tables")
def tables() -> str:
    return render_template(
        "tables.html",
        brokers=_broker_records(),
        tickers=_ticker_records(),
        markets=Market,
        contracts=_option_contracts(),
        expirations=_option_expirations(),
        option_types=OptionType,
    )


@bp.post("/tables/brokers")
def create_broker() -> ResponseReturnValue:
    try:
        data = parse_broker(request.form)
        duplicate = db.session.scalar(
            select(Broker).where(
                (func.lower(Broker.name) == data.name.lower())
                | (func.lower(Broker.acronym) == data.acronym.lower())
            )
        )
        if duplicate is not None:
            raise ValueError("O nome ou a sigla da corretora já está cadastrado.")
        db.session.add(Broker(**asdict(data)))
        db.session.commit()
        flash("Corretora adicionada.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("portfolio.tables"))


@bp.post("/tables/brokers/<int:broker_id>")
def update_broker(broker_id: int) -> ResponseReturnValue:
    broker = db.get_or_404(Broker, broker_id)
    try:
        data = parse_broker(request.form)
        duplicate = db.session.scalar(
            select(Broker).where(
                (func.lower(Broker.name) == data.name.lower())
                | (func.lower(Broker.acronym) == data.acronym.lower()),
                Broker.id != broker.id,
            )
        )
        if duplicate is not None:
            raise ValueError("O nome ou a sigla da corretora já está cadastrado.")
        broker.name = data.name
        broker.acronym = data.acronym
        db.session.commit()
        flash("Corretora atualizada.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("portfolio.tables"))


@bp.post("/tables/brokers/<int:broker_id>/delete")
def delete_broker(broker_id: int) -> ResponseReturnValue:
    broker = db.get_or_404(Broker, broker_id)
    db.session.delete(broker)
    try:
        db.session.commit()
        flash("Corretora excluída.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("A corretora não pode ser excluída enquanto possuir posições.", "error")
    return redirect(url_for("portfolio.tables"))


@bp.post("/tables/tickers")
def create_ticker() -> ResponseReturnValue:
    try:
        data = parse_ticker(request.form)
        if db.session.scalar(select(Ticker).where(Ticker.symbol == data.symbol)) is not None:
            raise ValueError("Esse ticker já está cadastrado.")
        db.session.add(Ticker(**asdict(data)))
        db.session.commit()
        flash("Ticker adicionado.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("portfolio.tables"))


@bp.post("/tables/tickers/<int:ticker_id>")
def update_ticker(ticker_id: int) -> ResponseReturnValue:
    ticker = db.get_or_404(Ticker, ticker_id)
    try:
        data = parse_ticker(request.form)
        duplicate = db.session.scalar(
            select(Ticker).where(Ticker.symbol == data.symbol, Ticker.id != ticker.id)
        )
        if duplicate is not None:
            raise ValueError("Esse ticker já está cadastrado.")
        for key, value in asdict(data).items():
            setattr(ticker, key, value)
        db.session.commit()
        flash("Ticker atualizado.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("portfolio.tables"))


@bp.post("/tables/tickers/<int:ticker_id>/delete")
def delete_ticker(ticker_id: int) -> ResponseReturnValue:
    ticker = db.get_or_404(Ticker, ticker_id)
    db.session.delete(ticker)
    try:
        db.session.commit()
        flash("Ticker excluído.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("O ticker não pode ser excluído enquanto possuir posições.", "error")
    return redirect(url_for("portfolio.tables"))


@bp.get("/positions/new")
def new_position() -> str:
    return render_template(
        "position_form.html",
        position=None,
        brokers=_broker_records(),
        tickers=_ticker_records(),
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
            brokers=_broker_records(),
            tickers=_ticker_records(),
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
        brokers=_broker_records(),
        tickers=_ticker_records(),
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
            brokers=_broker_records(),
            tickers=_ticker_records(),
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


@bp.get("/health")
def health() -> ResponseReturnValue:
    db.session.execute(select(1))
    return jsonify(status="ok")
