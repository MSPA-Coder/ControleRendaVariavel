from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import case, select
from sqlalchemy.orm import joinedload

from app import db
from app.domain import operation_result
from app.models import (
    Broker,
    OptionContract,
    OptionPosition,
    Portfolio,
    Position,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
)
from app.option_position_closure import (
    partial_close_of_open_position as option_partial_close_of_open_position,
)
from app.option_position_closure import (
    revert_partial_close as option_revert_partial_close,
)
from app.position_closure import partial_close_of_open_position, revert_partial_close
from app.routes import bp
from app.routes.helpers import (
    broker_records,
    investable_ticker_records,
    is_htmx_request,
    portfolio_records,
    selected_filters,
)
from app.validation import parse_finite_decimal


def _partial_close_source(transaction: Transaction) -> Position | OptionPosition | None:
    """A posição (ação ou opção) que esta transação encerrou parcialmente,
    ainda aberta — despacha para o par correto conforme o instrumento
    (``ticker_id`` para ação, ``option_contract_id`` para opção)."""
    if transaction.option_contract_id is not None:
        return option_partial_close_of_open_position(transaction)
    return partial_close_of_open_position(transaction)


def _revert_partial_close(transaction: Transaction) -> Position | OptionPosition | None:
    if transaction.option_contract_id is not None:
        return option_revert_partial_close(transaction)
    return revert_partial_close(transaction)


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
    portfolio_id: int
    notes: str | None


@dataclass(frozen=True, slots=True)
class TransactionPerformance:
    """Indicadores das transações encerradas em uma única moeda.

    Resultado monetário não pode ser somado entre BRL e USD sem uma taxa de
    câmbio. Os indicadores derivados desses valores (profit factor e payoff)
    também precisam permanecer dentro do mesmo agrupamento.
    """

    currency: str
    gain: Decimal
    loss: Decimal
    result: Decimal
    win_rate: Decimal | None
    profit_factor: Decimal | None
    payoff_ratio: Decimal | None
    avg_days_held: Decimal | None


def _transaction_performance_by_currency(
    records: list[Transaction],
) -> list[TransactionPerformance]:
    """Calcula o resumo de transações encerradas, separado por moeda."""
    closed_by_currency: dict[str, list[Transaction]] = {}
    for transaction in records:
        if transaction.status == TransactionStatus.CLOSED:
            closed_by_currency.setdefault(transaction.currency, []).append(transaction)

    summaries: list[TransactionPerformance] = []
    for currency in sorted(closed_by_currency):
        closed_records = closed_by_currency[currency]
        gains = [tx.result for tx in closed_records if tx.result is not None and tx.result > 0]
        losses = [tx.result for tx in closed_records if tx.result is not None and tx.result < 0]
        gain = sum(gains, Decimal("0"))
        loss = sum(losses, Decimal("0"))
        wins = len(gains)
        win_rate = Decimal(wins) / Decimal(len(closed_records)) if closed_records else None
        profit_factor = (gain / abs(loss)) if loss != 0 else None
        avg_gain = gain / Decimal(len(gains)) if gains else None
        avg_loss = abs(loss) / Decimal(len(losses)) if losses else None
        payoff_ratio = (avg_gain / avg_loss) if avg_gain is not None and avg_loss else None
        days_held = [tx.days_held for tx in closed_records if tx.days_held is not None]
        avg_days_held = (
            Decimal(sum(days_held)) / Decimal(len(days_held)) if days_held else None
        )
        summaries.append(
            TransactionPerformance(
                currency=currency,
                gain=gain,
                loss=loss,
                result=gain + loss,
                win_rate=win_rate,
                profit_factor=profit_factor,
                payoff_ratio=payoff_ratio,
                avg_days_held=avg_days_held,
            )
        )
    return summaries


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
    ticker = db.session.get(Ticker, ticker_id)
    if db.session.get(Broker, broker_id) is None or ticker is None:
        raise ValueError("Selecione uma corretora e um ticker cadastrados.")
    if ticker.is_benchmark:
        raise ValueError("Esse ticker está marcado como referência de comparação.")
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
        portfolio_id = int(raw["portfolio_id"])
    except (KeyError, ValueError) as exc:
        raise ValueError("Selecione uma carteira.") from exc
    if db.session.get(Portfolio, portfolio_id) is None:
        raise ValueError("Selecione uma carteira cadastrada.")
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
        portfolio_id,
        notes,
    )


def _build_transaction(data: TransactionInput) -> Transaction:
    result = operation_result(
        data.side.value, data.quantity, data.average_cost, data.exit_price, data.result_mode
    )
    fields = asdict(data)
    fields["result"] = result
    return Transaction(status=TransactionStatus.CLOSED, **fields)


def transactions_results_context() -> dict[str, object]:
    """Contexto da região de resultados de Transações.

    Compartilhado entre a página inteira e o fragmento atualizado por HTMX,
    para que os dois nunca divirjam.
    """
    portfolio_id, broker, selected_portfolio_id = selected_filters()
    # A tela abre nas transações **fechadas**: são as que têm resultado
    # realizado, que é o que se vem consultar aqui. As abertas espelham
    # posições que a Carteira já mostra.
    status_raw = request.args.get("status", TransactionStatus.CLOSED.value)
    status_order = case((Transaction.status == TransactionStatus.OPEN, 0), else_=1)
    statement = (
        select(Transaction)
        .join(Transaction.broker_ref)
        .options(
            joinedload(Transaction.ticker_ref),
            joinedload(Transaction.option_contract_ref).joinedload(OptionContract.ticker_ref),
            joinedload(Transaction.option_contract_ref).joinedload(OptionContract.expiration),
        )
        .order_by(
            status_order,
            Transaction.closed_on.desc(),
            Transaction.opened_on.desc(),
            Transaction.id.desc(),
        )
    )
    if portfolio_id is not None:
        statement = statement.where(Transaction.portfolio_id == portfolio_id)
    else:
        # "Todas" quer dizer todas as reais, como no filtro da Carteira.
        statement = statement.join(Transaction.portfolio_ref).where(
            Portfolio.simulated.is_(False)
        )
    if broker:
        statement = statement.where(Broker.name == broker)
    if status_raw != "all":
        try:
            status = TransactionStatus(status_raw)
            statement = statement.where(Transaction.status == status)
        except ValueError:
            status_raw = "all"
    records = list(db.session.scalars(statement))
    # Transações que são o encerramento parcial de uma posição ainda aberta:
    # elas não se editam soltas, e excluí-las devolve a quantidade à posição.
    # Duas consultas só (uma por instrumento), em vez de uma por linha da
    # tabela. ``source_position_id`` é ambíguo sozinho — ``Position`` e
    # ``OptionPosition`` têm sequências de id independentes — por isso o
    # cruzamento é feito separadamente por tipo de instrumento.
    live_position_ids = set(db.session.scalars(select(Position.id)))
    live_option_position_ids = set(db.session.scalars(select(OptionPosition.id)))
    partial_close_ids = {
        transaction.id
        for transaction in records
        if transaction.status == TransactionStatus.CLOSED
        and transaction.source_position_id is not None
        and (
            (
                transaction.option_contract_id is None
                and transaction.source_position_id in live_position_ids
            )
            or (
                transaction.option_contract_id is not None
                and transaction.source_position_id in live_option_position_ids
            )
        )
    }
    # KPIs de performance só fazem sentido para transações já encerradas —
    # linhas abertas (status=OPEN) não têm resultado realizado ainda, então
    # ficam de fora dessas contas independentemente do filtro de status
    # escolhido acima (que afeta só o que aparece na tabela). Cada resumo é
    # separado por moeda: BRL e USD não são somáveis sem uma taxa de câmbio.
    performance_by_currency = _transaction_performance_by_currency(records)
    return {
        "transactions": records,
        "partial_close_ids": partial_close_ids,
        "selected_broker": broker or "",
        "selected_portfolio_id": selected_portfolio_id,
        "portfolios": portfolio_records(),
        "selected_status": status_raw,
        "transaction_statuses": TransactionStatus,
        "performance_by_currency": performance_by_currency,
    }


@bp.get("/transactions")
def transactions() -> str:
    """Transações: página inteira, ou só a região de resultados para o HTMX.

    A mesma URL serve os dois casos, então o filtro pode empurrar ao
    histórico o endereço real da página (`/transactions?...`) em vez do
    endereço de um fragmento. `HX-Request` decide apenas a forma da
    resposta; a autorização é idêntica nos dois caminhos.
    """
    results = transactions_results_context()
    if is_htmx_request():
        return render_template("partials/transactions_results.html", **results)
    return render_template(
        "transactions.html",
        brokers=broker_records(),
        **results,
    )


@bp.get("/transactions/new")
def new_transaction() -> str:
    return render_template(
        "transaction_form.html",
        transaction=None,
        brokers=broker_records(),
        tickers=investable_ticker_records(),
        sides=Side,
        portfolios=portfolio_records(),
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
            tickers=investable_ticker_records(),
            sides=Side,
            portfolios=portfolio_records(),
        ), 422
    db.session.add(_build_transaction(data))
    db.session.commit()
    flash("Transação registrada.", "success")
    return redirect(url_for("portfolio.transactions"))


def _partial_close_edit_guard(transaction: Transaction) -> ResponseReturnValue | None:
    """Impede editar por aqui o encerramento parcial de uma posição viva.

    Esses campos não são independentes: quantidade, preço de saída e resultado
    espelham um movimento da posição. Editá-los soltos deixaria a posição e o
    extrato descrevendo quantidades diferentes — a mesma inconsistência que
    excluir a transação sem devolver a quantidade causava.
    """
    position = _partial_close_source(transaction)
    if position is None:
        return None
    flash(
        "Essa transação é o encerramento parcial de uma posição ainda aberta. "
        "Para corrigi-la, exclua-a — a quantidade volta para a posição — e "
        "lance o encerramento de novo.",
        "error",
    )
    return redirect(url_for("portfolio.transactions"))


def _option_edit_guard(transaction: Transaction) -> ResponseReturnValue | None:
    """Bloqueia a edição de uma transação de opção pelo formulário genérico
    de ações (``transaction_form.html`` espera ``ticker_id``, que é ``None``
    numa transação de opção). Servidor é quem decide, não a interface — a
    grade de Transações já esconde o link de "editar" para essas linhas, mas
    a rota recusa mesmo com um POST direto."""
    if transaction.option_contract_id is None:
        return None
    flash(
        "Essa transação é de uma opção e não é editada por este formulário; "
        "exclua-a e relance o encerramento, se precisar corrigi-la.",
        "error",
    )
    return redirect(url_for("portfolio.transactions"))


@bp.get("/transactions/<int:transaction_id>/edit")
def edit_transaction(transaction_id: int) -> ResponseReturnValue:
    transaction = db.get_or_404(Transaction, transaction_id)
    if transaction.status == TransactionStatus.OPEN:
        if transaction.source_position_id is not None:
            edit_endpoint = (
                "options.edit_position"
                if transaction.option_contract_id is not None
                else "portfolio.edit_position"
            )
            flash("Essa transação está aberta; edite-a pela posição.", "error")
            return redirect(
                url_for(edit_endpoint, position_id=transaction.source_position_id)
            )
        flash("Essa transação está aberta e não pode ser editada por aqui.", "error")
        return redirect(url_for("portfolio.transactions"))
    blocked = _option_edit_guard(transaction) or _partial_close_edit_guard(transaction)
    if blocked is not None:
        return blocked
    return render_template(
        "transaction_form.html",
        transaction=transaction,
        brokers=broker_records(),
        tickers=investable_ticker_records(),
        sides=Side,
        portfolios=portfolio_records(),
    )


@bp.post("/transactions/<int:transaction_id>")
def update_transaction(transaction_id: int) -> ResponseReturnValue:
    transaction = db.get_or_404(Transaction, transaction_id)
    if transaction.status == TransactionStatus.OPEN:
        flash("Essa transação está aberta; edite-a pela posição.", "error")
        return redirect(url_for("portfolio.transactions"))
    blocked = _option_edit_guard(transaction) or _partial_close_edit_guard(transaction)
    if blocked is not None:
        return blocked
    try:
        data = _parse_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "transaction_form.html",
            transaction=request.form,
            edit_mode=True,
            transaction_id=transaction_id,
            brokers=broker_records(),
            tickers=investable_ticker_records(),
            sides=Side,
            portfolios=portfolio_records(),
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
    """Exclui uma transação encerrada.

    Quando ela é o encerramento parcial de uma posição ainda aberta, excluí-la
    é desfazer a operação: a quantidade encerrada volta para a posição e a
    baixa sai do extrato. Sem isso, sumiria o registro da venda mas a posição
    continuaria reduzida — Carteira e Transações passariam a mostrar uma
    quantidade que não corresponde a nenhum lançamento.
    """
    transaction = db.get_or_404(Transaction, transaction_id)
    if transaction.status == TransactionStatus.OPEN:
        flash("Essa transação está aberta; exclua a posição.", "error")
        return redirect(url_for("portfolio.transactions"))
    reverted = _partial_close_source(transaction) is not None
    if reverted:
        _revert_partial_close(transaction)
    db.session.delete(transaction)
    db.session.commit()
    if reverted:
        flash(
            "Encerramento parcial desfeito: a quantidade voltou para a posição em Carteira.",
            "success",
        )
    else:
        flash("Transação excluída.", "success")
    return redirect(url_for("portfolio.transactions"))
