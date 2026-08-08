from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import select

from app import db
from app.dividend_report import build_dividend_report
from app.models import Broker, Dividend, Ticker
from app.routes import bp
from app.routes.helpers import (
    broker_records,
    investable_ticker_records,
    open_real_cost_basis_by_ticker,
)
from app.validation import parse_finite_decimal


@dataclass(frozen=True, slots=True)
class DividendInput:
    broker_id: int
    ticker_id: int
    amount: Decimal
    payment_date: date
    notes: str | None


def _parse_form() -> DividendInput:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        broker_id = int(raw["broker_id"])
        ticker_id = int(raw["ticker_id"])
        amount = parse_finite_decimal(raw["amount"], field_name="um valor de provento")
        payment_date = date.fromisoformat(raw["payment_date"])
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("Há um valor ausente ou inválido no formulário.") from exc
    ticker = db.session.get(Ticker, ticker_id)
    if db.session.get(Broker, broker_id) is None or ticker is None:
        raise ValueError("Selecione uma corretora e um ticker cadastrados.")
    if ticker.is_benchmark:
        raise ValueError("Esse ticker está marcado como referência de comparação.")
    if amount <= 0:
        raise ValueError("O valor do provento deve ser positivo.")
    notes = raw.get("notes") or None
    return DividendInput(broker_id, ticker_id, amount, payment_date, notes)


@bp.get("/dividends")
def dividends() -> str:
    broker = request.args.get("broker") or None
    statement = (
        select(Dividend)
        .join(Dividend.broker_ref)
        .join(Dividend.ticker_ref)
        .order_by(Dividend.payment_date.desc(), Dividend.id.desc())
    )
    if broker:
        statement = statement.where(Broker.name == broker)
    records = list(db.session.scalars(statement))
    totals_by_currency: dict[str, Decimal] = {}
    for record in records:
        totals_by_currency[record.currency] = (
            totals_by_currency.get(record.currency, Decimal("0")) + record.amount
        )
    report = build_dividend_report(records, open_real_cost_basis_by_ticker())
    return render_template(
        "dividends.html",
        dividends=records,
        brokers=broker_records(),
        selected_broker=broker or "",
        totals_by_currency=sorted(totals_by_currency.items()),
        report=report,
    )


@bp.get("/dividends/new")
def new_dividend() -> str:
    return render_template(
        "dividend_form.html",
        dividend=None,
        brokers=broker_records(),
        tickers=investable_ticker_records(),
    )


@bp.post("/dividends")
def create_dividend() -> ResponseReturnValue:
    try:
        data = _parse_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "dividend_form.html",
            dividend=request.form,
            brokers=broker_records(),
            tickers=investable_ticker_records(),
        ), 422
    db.session.add(Dividend(**asdict(data)))
    db.session.commit()
    flash("Provento registrado.", "success")
    return redirect(url_for("portfolio.dividends"))


@bp.get("/dividends/<int:dividend_id>/edit")
def edit_dividend(dividend_id: int) -> str:
    dividend = db.get_or_404(Dividend, dividend_id)
    return render_template(
        "dividend_form.html",
        dividend=dividend,
        brokers=broker_records(),
        tickers=investable_ticker_records(),
    )


@bp.post("/dividends/<int:dividend_id>")
def update_dividend(dividend_id: int) -> ResponseReturnValue:
    dividend = db.get_or_404(Dividend, dividend_id)
    try:
        data = _parse_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "dividend_form.html",
            dividend=request.form,
            brokers=broker_records(),
            tickers=investable_ticker_records(),
        ), 422
    for key, value in asdict(data).items():
        setattr(dividend, key, value)
    db.session.commit()
    flash("Provento atualizado.", "success")
    return redirect(url_for("portfolio.dividends"))


@bp.post("/dividends/<int:dividend_id>/delete")
def delete_dividend(dividend_id: int) -> ResponseReturnValue:
    dividend = db.get_or_404(Dividend, dividend_id)
    db.session.delete(dividend)
    db.session.commit()
    flash("Provento excluído.", "success")
    return redirect(url_for("portfolio.dividends"))
