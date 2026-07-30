from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

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
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app import db
from app.models import (
    AppSetting,
    Broker,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionType,
    PositionKind,
    Side,
    Ticker,
)
from app.option_portfolio import build_option_portfolio
from app.pricing_settings import DEFAULT_RISK_FREE_RATE_ANNUAL

bp = Blueprint("options", __name__)


@dataclass(frozen=True, slots=True)
class OptionPositionInput:
    broker_id: int
    contract_id: int
    quantity: Decimal
    average_cost: Decimal
    target_price: Decimal | None
    side: Side
    opened_on: date
    result_mode: str
    position_kind: PositionKind


def _positions() -> list[OptionPosition]:
    statement = (
        select(OptionPosition)
        .options(
            joinedload(OptionPosition.broker_ref),
            joinedload(OptionPosition.quote),
            joinedload(OptionPosition.contract).joinedload(OptionContract.ticker_ref),
            joinedload(OptionPosition.contract).joinedload(
                OptionContract.underlying_ticker_ref
            ),
            joinedload(OptionPosition.contract)
            .joinedload(OptionContract.expiration),
        )
        .order_by(OptionPosition.opened_on, OptionPosition.id)
    )
    return list(db.session.scalars(statement).unique())


def _brokers() -> list[Broker]:
    return list(db.session.scalars(select(Broker).order_by(Broker.name)))


def _tickers() -> list[Ticker]:
    return list(db.session.scalars(select(Ticker).order_by(Ticker.symbol)))


def _expirations() -> list[OptionExpiration]:
    return list(
        db.session.scalars(
            select(OptionExpiration).order_by(OptionExpiration.exercise_date)
        )
    )


def _contracts() -> list[OptionContract]:
    statement = (
        select(OptionContract)
        .options(
            joinedload(OptionContract.ticker_ref),
            joinedload(OptionContract.underlying_ticker_ref),
            joinedload(OptionContract.expiration),
        )
        .order_by(OptionExpiration.exercise_date, OptionContract.id)
        .join(OptionContract.expiration)
    )
    return list(db.session.scalars(statement).unique())


def _parse_position() -> OptionPositionInput:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        broker_id = int(raw["broker_id"])
        contract_id = int(raw["contract_id"])
        quantity = Decimal(raw["quantity"])
        average_cost = Decimal(raw["average_cost"])
        target_price = Decimal(raw["target_price"]) if raw.get("target_price") else None
        side = Side(raw["side"])
        opened_on = date.fromisoformat(raw["opened_on"])
        position_kind = PositionKind(raw.get("position_kind", PositionKind.REAL.value))
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("Há um valor ausente ou inválido no formulário.") from exc
    if db.session.get(Broker, broker_id) is None:
        raise ValueError("Selecione uma corretora cadastrada.")
    if db.session.get(OptionContract, contract_id) is None:
        raise ValueError("Selecione um contrato de opção cadastrado.")
    if quantity <= 0 or average_cost < 0 or (
        target_price is not None and target_price < 0
    ):
        raise ValueError("Quantidade deve ser positiva e preços não podem ser negativos.")
    result_mode = raw.get("result_mode", "").upper()
    if result_mode not in {"L", "B"}:
        raise ValueError("Modo de resultado inválido.")
    return OptionPositionInput(
        broker_id,
        contract_id,
        quantity,
        average_cost,
        target_price,
        side,
        opened_on,
        result_mode,
        position_kind,
    )


@bp.get("/options")
def index() -> str:
    settings = db.session.get(AppSetting, 1)
    risk_free_rate = (
        settings.risk_free_rate_annual if settings else DEFAULT_RISK_FREE_RATE_ANNUAL
    )
    portfolio = build_option_portfolio(
        _positions(),
        stale_after_seconds=current_app.config["RTD_STALE_AFTER_SECONDS"],
        risk_free_rate_annual=risk_free_rate,
    )
    return render_template(
        "options.html",
        portfolio=portfolio,
        poll_interval_seconds=settings.poll_interval_seconds if settings else 2,
    )


@bp.get("/api/options")
def api() -> ResponseReturnValue:
    settings = db.session.get(AppSetting, 1)
    risk_free_rate = (
        settings.risk_free_rate_annual if settings else DEFAULT_RISK_FREE_RATE_ANNUAL
    )
    portfolio = build_option_portfolio(
        _positions(),
        stale_after_seconds=current_app.config["RTD_STALE_AFTER_SECONDS"],
        risk_free_rate_annual=risk_free_rate,
    )
    return jsonify(
        rows=len(portfolio.positions),
        result=str(portfolio.result),
        poll_interval_seconds=settings.poll_interval_seconds if settings else 2,
    )


@bp.get("/options/new")
def new_position() -> str:
    return render_template(
        "option_form.html",
        position=None,
        brokers=_brokers(),
        contracts=_contracts(),
        sides=Side,
        position_kinds=PositionKind,
    )


@bp.post("/options/positions")
def create_position() -> ResponseReturnValue:
    try:
        data = _parse_position()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "option_form.html",
            position=request.form,
            brokers=_brokers(),
            contracts=_contracts(),
            sides=Side,
            position_kinds=PositionKind,
        ), 422
    db.session.add(OptionPosition(**asdict(data)))
    db.session.commit()
    flash("Posição de opção adicionada.", "success")
    return redirect(url_for("options.index"))


@bp.get("/options/positions/<int:position_id>/edit")
def edit_position(position_id: int) -> str:
    return render_template(
        "option_form.html",
        position=db.get_or_404(OptionPosition, position_id),
        brokers=_brokers(),
        contracts=_contracts(),
        sides=Side,
        position_kinds=PositionKind,
    )


@bp.post("/options/positions/<int:position_id>")
def update_position(position_id: int) -> ResponseReturnValue:
    position = db.get_or_404(OptionPosition, position_id)
    try:
        data = _parse_position()
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("options.edit_position", position_id=position_id))
    for key, value in asdict(data).items():
        setattr(position, key, value)
    db.session.commit()
    flash("Posição de opção atualizada.", "success")
    return redirect(url_for("options.index"))


@bp.post("/options/positions/<int:position_id>/delete")
def delete_position(position_id: int) -> ResponseReturnValue:
    db.session.delete(db.get_or_404(OptionPosition, position_id))
    db.session.commit()
    flash("Posição de opção excluída.", "success")
    return redirect(url_for("options.index"))


@bp.get("/tables/options")
def tables() -> ResponseReturnValue:
    return redirect(f"{url_for('portfolio.tables')}#options")


@bp.post("/tables/options/expirations")
def create_expiration() -> ResponseReturnValue:
    try:
        call_code = request.form["call_code"].strip().upper()
        put_code = request.form["put_code"].strip().upper()
        exercise_date = date.fromisoformat(request.form["exercise_date"])
        if len(call_code) != 5 or len(put_code) != 5:
            raise ValueError("Os códigos devem seguir o formato 2026A.")
        db.session.add(
            OptionExpiration(
                call_code=call_code,
                put_code=put_code,
                exercise_date=exercise_date,
            )
        )
        db.session.commit()
        flash("Vencimento adicionado.", "success")
    except (KeyError, ValueError, IntegrityError):
        db.session.rollback()
        flash("Vencimento inválido ou já cadastrado.", "error")
    return redirect(url_for("options.tables"))


@bp.post("/tables/options/expirations/<int:expiration_id>/delete")
def delete_expiration(expiration_id: int) -> ResponseReturnValue:
    db.session.delete(db.get_or_404(OptionExpiration, expiration_id))
    try:
        db.session.commit()
        flash("Vencimento excluído.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("O vencimento possui contratos e não pode ser excluído.", "error")
    return redirect(url_for("options.tables"))


@bp.post("/tables/options/expirations/<int:expiration_id>")
def update_expiration(expiration_id: int) -> ResponseReturnValue:
    expiration = db.get_or_404(OptionExpiration, expiration_id)
    try:
        call_code = request.form["call_code"].strip().upper()
        put_code = request.form["put_code"].strip().upper()
        exercise_date = date.fromisoformat(request.form["exercise_date"])
        if len(call_code) != 5 or len(put_code) != 5:
            raise ValueError
        expiration.call_code = call_code
        expiration.put_code = put_code
        expiration.exercise_date = exercise_date
        db.session.commit()
        flash("Vencimento atualizado.", "success")
    except (KeyError, ValueError, IntegrityError):
        db.session.rollback()
        flash("Vencimento inválido ou duplicado.", "error")
    return redirect(url_for("options.tables"))


@bp.post("/tables/options/contracts")
def create_contract() -> ResponseReturnValue:
    try:
        ticker_id = int(request.form["ticker_id"])
        underlying_ticker_id = int(request.form["underlying_ticker_id"])
        expiration_id = int(request.form["expiration_id"])
        option_type = OptionType(request.form["option_type"])
        strike = Decimal(request.form["strike"])
        if strike < 0 or ticker_id == underlying_ticker_id:
            raise ValueError
        db.session.add(
            OptionContract(
                ticker_id=ticker_id,
                underlying_ticker_id=underlying_ticker_id,
                expiration_id=expiration_id,
                option_type=option_type,
                strike=strike,
            )
        )
        db.session.commit()
        flash("Contrato de opção adicionado.", "success")
    except (KeyError, ValueError, ArithmeticError, IntegrityError):
        db.session.rollback()
        flash("Contrato inválido ou ticker já associado a uma opção.", "error")
    return redirect(url_for("options.tables"))


@bp.post("/tables/options/contracts/<int:contract_id>/delete")
def delete_contract(contract_id: int) -> ResponseReturnValue:
    db.session.delete(db.get_or_404(OptionContract, contract_id))
    try:
        db.session.commit()
        flash("Contrato excluído.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("O contrato possui posições e não pode ser excluído.", "error")
    return redirect(url_for("options.tables"))


@bp.post("/tables/options/contracts/<int:contract_id>")
def update_contract(contract_id: int) -> ResponseReturnValue:
    contract = db.get_or_404(OptionContract, contract_id)
    try:
        ticker_id = int(request.form["ticker_id"])
        underlying_ticker_id = int(request.form["underlying_ticker_id"])
        strike = Decimal(request.form["strike"])
        if strike < 0 or ticker_id == underlying_ticker_id:
            raise ValueError
        contract.ticker_id = ticker_id
        contract.underlying_ticker_id = underlying_ticker_id
        contract.expiration_id = int(request.form["expiration_id"])
        contract.option_type = OptionType(request.form["option_type"])
        contract.strike = strike
        db.session.commit()
        flash("Contrato atualizado.", "success")
    except (KeyError, ValueError, ArithmeticError, IntegrityError):
        db.session.rollback()
        flash("Contrato inválido ou ticker já associado a uma opção.", "error")
    return redirect(url_for("options.tables"))
