from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import select

from app import db
from app.domain import operation_result
from app.models import Broker, PositionKind, Side, Ticker, Transaction
from app.routes import bp
from app.routes.helpers import broker_records, ticker_records
from app.validation import parse_finite_decimal


@dataclass(frozen=True, slots=True)
class TransactionInput:
    broker_id: int
    ticker_id: int
    quantity: Decimal
    average_cost: Decimal
    exit_price: Decimal
    side: Side
    opened_on: date
    closed_on: date
    result_mode: str
    position_kind: PositionKind
    notes: str | None


def _parse_form() -> TransactionInput:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        broker_id = int(raw["broker_id"])
        ticker_id = int(raw["ticker_id"])
        quantity = parse_finite_decimal(raw["quantity"], field_name="uma quantidade")
        average_cost = parse_finite_decimal(raw["average_cost"], field_name="um custo médio")
        exit_price = parse_finite_decimal(raw["exit_price"], field_name="um preço de saída")
        opened_on = date.fromisoformat(raw["opened_on"])
        closed_on = date.fromisoformat(raw["closed_on"])
        side = Side(raw["side"])
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("Há um valor ausente ou inválido no formulário.") from exc
    if db.session.get(Broker, broker_id) is None or db.session.get(Ticker, ticker_id) is None:
        raise ValueError("Selecione uma corretora e um ticker cadastrados.")
    if quantity <= 0 or average_cost < 0 or exit_price < 0:
        raise ValueError(
            "Quantidade deve ser positiva; custo e preço de saída não podem ser negativos."
        )
    if closed_on < opened_on:
        raise ValueError("A data de encerramento não pode ser anterior à data de abertura.")
    result_mode = raw.get("result_mode", "").upper()
    if result_mode not in {"L", "B"}:
        raise ValueError("Modo de resultado inválido.")
    try:
        position_kind = PositionKind(raw.get("position_kind", PositionKind.REAL.value))
    except ValueError as exc:
        raise ValueError("Tipo de posição inválido.") from exc
    notes = raw.get("notes") or None
    return TransactionInput(
        broker_id,
        ticker_id,
        quantity,
        average_cost,
        exit_price,
        side,
        opened_on,
        closed_on,
        result_mode,
        position_kind,
        notes,
    )


def _build_transaction(data: TransactionInput) -> Transaction:
    result = operation_result(
        data.side.value, data.quantity, data.average_cost, data.exit_price, data.result_mode
    )
    fields = asdict(data)
    fields["result"] = result
    return Transaction(**fields)


@bp.get("/transactions")
def transactions() -> str:
    position_kind_raw = request.args.get("position_kind", "all")
    broker = request.args.get("broker") or None
    statement = (
        select(Transaction)
        .join(Transaction.broker_ref)
        .join(Transaction.ticker_ref)
        .order_by(Transaction.closed_on.desc(), Transaction.id.desc())
    )
    if position_kind_raw != "all":
        try:
            kind = PositionKind(position_kind_raw)
            statement = statement.where(Transaction.position_kind == kind)
        except ValueError:
            position_kind_raw = "all"
    if broker:
        statement = statement.where(Broker.name == broker)
    records = list(db.session.scalars(statement))
    gains = [tx.result for tx in records if tx.result > 0]
    losses = [tx.result for tx in records if tx.result < 0]
    gain = sum(gains, Decimal("0"))
    loss = sum(losses, Decimal("0"))
    wins = len(gains)
    win_rate = Decimal(wins) / Decimal(len(records)) if records else None
    profit_factor = (gain / abs(loss)) if loss != 0 else None
    avg_gain = gain / Decimal(len(gains)) if gains else None
    avg_loss = abs(loss) / Decimal(len(losses)) if losses else None
    # Payoff ratio (item 5/Fase C2): média dos ganhos sobre a média das
    # perdas, em módulo. Só é calculável quando há ao menos uma transação
    # de cada sinal; caso contrário fica sem sentido (nunca ganhou, ou
    # nunca perdeu, então não há uma "razão" a expressar).
    payoff_ratio = (avg_gain / avg_loss) if avg_gain is not None and avg_loss else None
    avg_days_held = (
        Decimal(sum(tx.days_held for tx in records)) / Decimal(len(records))
        if records
        else None
    )
    return render_template(
        "transactions.html",
        transactions=records,
        brokers=broker_records(),
        selected_broker=broker or "",
        selected_kind=position_kind_raw,
        position_kinds=PositionKind,
        gain=gain,
        loss=loss,
        result=gain + loss,
        win_rate=win_rate,
        profit_factor=profit_factor,
        payoff_ratio=payoff_ratio,
        avg_days_held=avg_days_held,
    )


@bp.get("/transactions/new")
def new_transaction() -> str:
    return render_template(
        "transaction_form.html",
        transaction=None,
        brokers=broker_records(),
        tickers=ticker_records(),
        sides=Side,
        position_kinds=PositionKind,
    )


@bp.post("/transactions")
def create_transaction() -> ResponseReturnValue:
    try:
        data = _parse_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "transaction_form.html",
            transaction=request.form,
            brokers=broker_records(),
            tickers=ticker_records(),
            sides=Side,
            position_kinds=PositionKind,
        ), 422
    db.session.add(_build_transaction(data))
    db.session.commit()
    flash("Transação registrada.", "success")
    return redirect(url_for("portfolio.transactions"))


@bp.get("/transactions/<int:transaction_id>/edit")
def edit_transaction(transaction_id: int) -> str:
    transaction = db.get_or_404(Transaction, transaction_id)
    return render_template(
        "transaction_form.html",
        transaction=transaction,
        brokers=broker_records(),
        tickers=ticker_records(),
        sides=Side,
        position_kinds=PositionKind,
    )


@bp.post("/transactions/<int:transaction_id>")
def update_transaction(transaction_id: int) -> ResponseReturnValue:
    transaction = db.get_or_404(Transaction, transaction_id)
    try:
        data = _parse_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "transaction_form.html",
            transaction=request.form,
            brokers=broker_records(),
            tickers=ticker_records(),
            sides=Side,
            position_kinds=PositionKind,
        ), 422
    updated = _build_transaction(data)
    for key, value in asdict(data).items():
        setattr(transaction, key, value)
    transaction.result = updated.result
    db.session.commit()
    flash("Transação atualizada.", "success")
    return redirect(url_for("portfolio.transactions"))


@bp.post("/transactions/<int:transaction_id>/delete")
def delete_transaction(transaction_id: int) -> ResponseReturnValue:
    transaction = db.get_or_404(Transaction, transaction_id)
    db.session.delete(transaction)
    db.session.commit()
    flash("Transação excluída.", "success")
    return redirect(url_for("portfolio.transactions"))
