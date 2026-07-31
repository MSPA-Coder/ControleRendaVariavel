from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import select

from app import db
from app.dividend_report import build_dividend_report
from app.models import Broker, Dividend, Position, PositionKind, Ticker
from app.routes import bp
from app.routes.helpers import broker_records, ticker_records


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
        amount = Decimal(raw["amount"])
        payment_date = date.fromisoformat(raw["payment_date"])
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("Há um valor ausente ou inválido no formulário.") from exc
    if db.session.get(Broker, broker_id) is None or db.session.get(Ticker, ticker_id) is None:
        raise ValueError("Selecione uma corretora e um ticker cadastrados.")
    if amount <= 0:
        raise ValueError("O valor do provento deve ser positivo.")
    notes = raw.get("notes") or None
    return DividendInput(broker_id, ticker_id, amount, payment_date, notes)


def _cost_basis_by_ticker() -> dict[int, Decimal]:
    """Custo de aquisição atual por ticker: soma de quantidade × custo
    médio das posições REAIS ainda abertas (item 5, Relatório de
    Proventos: base de cálculo do "yield on cost").

    Deliberadamente não filtra por corretora nem pelo filtro da página
    (``broker``): representa a base de custo total do ativo hoje, não uma
    fatia por corretora — um provento é um evento do ativo, não da
    corretora que o pagou. Tickers sem posição REAL aberta simplesmente
    não aparecem no mapeamento resultante.
    """
    statement = select(Position.ticker_id, Position.quantity, Position.average_cost).where(
        Position.position_kind == PositionKind.REAL
    )
    totals: dict[int, Decimal] = {}
    for ticker_id, quantity, average_cost in db.session.execute(statement):
        totals[ticker_id] = totals.get(ticker_id, Decimal("0")) + quantity * average_cost
    return totals


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
    report = build_dividend_report(records, _cost_basis_by_ticker())
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
        "dividend_form.html", dividend=None, brokers=broker_records(), tickers=ticker_records()
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
            tickers=ticker_records(),
        ), 422
    db.session.add(Dividend(**asdict(data)))
    db.session.commit()
    flash("Provento registrado.", "success")
    return redirect(url_for("portfolio.dividends"))


@bp.get("/dividends/<int:dividend_id>/edit")
def edit_dividend(dividend_id: int) -> str:
    dividend = db.get_or_404(Dividend, dividend_id)
    return render_template(
        "dividend_form.html", dividend=dividend, brokers=broker_records(), tickers=ticker_records()
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
            tickers=ticker_records(),
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
