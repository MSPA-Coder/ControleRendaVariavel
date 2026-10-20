"""Aplicar às posições as execuções do myProfit que ainda não estão no CRV.

O myProfit lê a B3 e é a fonte dos negócios; o CRV guarda as posições. Este
módulo fecha o ciclo: cada execução em escopo (ações, opções e ETF da B3) ainda
pendente vira abertura, reforço ou encerramento de posição pelos mesmos
serviços que a tela usa (``create_or_merge_position`` e
``apply_position_closure``), e a execução fica marcada para nunca ser aplicada
de novo.

Regras:

* **agrupamento**: execuções do mesmo pregão, corretora, ativo e lado formam um
  negócio só (o myProfit quebra uma ordem em várias linhas); o preço é a média
  ponderada **sem taxas**, a convenção do CRV;
* **já lançada**: se o CRV já tem o negócio (digitado à mão: compra com
  movimento ou lote aberto no mesmo dia e quantidade; venda com lotes fechados
  no mesmo dia somando a quantidade), a execução é só marcada, sem mexer na
  posição;
* **pendência** (nada é gravado, e a próxima rodada tenta de novo): ativo sem
  cadastro, opção sem contrato (o relatório não traz strike nem vencimento),
  exercício de opção (o custo do papel exercido é decisão do mantenedor),
  venda sem posição comprada que a cubra (lançamento de opção ou venda a
  descoberto), carteira ambígua;
* **linha de base**: as execuções anteriores à adoção, já conferidas pela
  conciliação, são marcadas por ``mark_baseline`` e nunca reaplicadas.

O ensaio executa tudo de verdade dentro de uma transação e a desfaz no fim:
o que ele mostra é exatamente o que a gravação faria. Cada negócio roda num
ponto de salvamento, e uma falha só desfaz o dele.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select

from app import db
from app.accounts.entitlements import grant_ticker_entitlement
from app.core.domain import Side
from app.models import (
    Execution,
    Market,
    OptionContract,
    OptionPosition,
    OptionPositionMovement,
    Portfolio,
    Position,
    PositionMovement,
    PositionMovementKind,
    Ticker,
    Transaction,
    TransactionStatus,
)
from app.options import closure as options
from app.positions import closure as stocks

APPLIED = "aplicada"
ALREADY_ENTERED = "ja_lancada"
BASELINE = "linha_de_base"

APPLICATION_LABELS = {
    APPLIED: "Aplicada",
    ALREADY_ENTERED: "Já lançada",
    BASELINE: "Linha de base",
}

_STOCK_GROUPS = frozenset({"acao", "etf"})
_OPTION_GROUP = "opcao"


@dataclass(frozen=True, slots=True)
class Trade:
    """Execuções do mesmo pregão, corretora, ativo e lado, somadas."""

    trade_date: date
    broker_id: int
    asset: str
    asset_group: str
    side: str
    quantity: Decimal
    price: Decimal
    fees: Decimal
    currency: str
    execution_ids: tuple[int, ...]
    first_line: tuple[str, int]

    @property
    def is_buy(self) -> bool:
        return self.side == "C"

    @property
    def label(self) -> str:
        verb = "compra" if self.is_buy else "venda"
        return f"{self.trade_date:%d/%m/%Y} {verb} de {self.quantity.normalize():f} {self.asset}"


def group_trades(executions: Iterable[Execution]) -> list[Trade]:
    """Agrupa por (pregão, corretora, ativo, lado), na ordem do relatório.

    Quantidade em módulo; preço médio ponderado sem taxas.
    """
    buckets: dict[tuple[date, int, str, str], list[Execution]] = defaultdict(list)
    for execution in executions:
        buckets[(execution.trade_date, execution.broker_id, execution.asset.upper(), execution.side)].append(execution)
    trades = []
    for (trade_date, broker_id, asset, side), rows in buckets.items():
        quantity = sum((abs(row.quantity) for row in rows), Decimal(0))
        notional = sum((abs(row.quantity) * row.price_without_fees for row in rows), Decimal(0))
        rows.sort(key=lambda row: (row.document, row.line_order))
        trades.append(
            Trade(
                trade_date=trade_date,
                broker_id=broker_id,
                asset=asset,
                asset_group=rows[0].asset_group,
                side=side,
                quantity=quantity,
                price=notional / quantity,
                fees=sum((row.fees_total for row in rows), Decimal(0)),
                currency=rows[0].currency,
                execution_ids=tuple(row.id for row in rows),
                first_line=(rows[0].document, rows[0].line_order),
            )
        )
    return sorted(trades, key=lambda trade: (trade.trade_date, trade.first_line))


def is_exercise(asset: str, asset_group: str) -> bool:
    """O myProfit registra o exercício como negócio da série com ``E`` no fim
    (``RAIZO600E``), e não como compra ou venda do papel."""
    return asset_group == _OPTION_GROUP and asset.upper().endswith("E")


@dataclass(slots=True)
class Outcome:
    trade: Trade
    #: ``aplicada``, ``ja_lancada`` ou ``pendente``.
    state: str
    detail: str


PENDING = "pendente"


@dataclass(slots=True)
class ApplicationReport:
    outcomes: list[Outcome] = field(default_factory=list)

    def of(self, state: str) -> list[Outcome]:
        return [outcome for outcome in self.outcomes if outcome.state == state]

    @property
    def pending(self) -> list[Outcome]:
        return self.of(PENDING)


class _PendingError(Exception):
    """Negócio que não pode ser aplicado sem decisão do mantenedor."""


@dataclass(frozen=True, slots=True)
class _Instrument:
    is_option: bool
    ticker: Ticker
    contract: OptionContract | None


def _instrument(trade: Trade) -> _Instrument:
    if is_exercise(trade.asset, trade.asset_group):
        raise _PendingError(
            "exercício de opção: registre à mão o papel recebido ou entregue (custo líquido do prêmio) "
            "e marque a execução com marcar-execucoes-aplicadas"
        )
    ticker = db.session.scalar(
        select(Ticker).where(func.upper(Ticker.symbol) == trade.asset, Ticker.market == Market.B3)
    )
    if trade.asset_group in _STOCK_GROUPS:
        if ticker is None:
            raise _PendingError(f"{trade.asset} não está cadastrado no CRV")
        return _Instrument(False, ticker, None)
    if trade.asset_group == _OPTION_GROUP:
        contract = (
            db.session.scalar(select(OptionContract).where(OptionContract.ticker_id == ticker.id))
            if ticker is not None
            else None
        )
        if ticker is None or contract is None:
            raise _PendingError(f"opção {trade.asset} sem contrato no CRV (cadastre strike e vencimento)")
        return _Instrument(True, ticker, contract)
    raise _PendingError(f"grupo {trade.asset_group} fora do escopo")


def _open_positions(owner_id: int, trade: Trade, instrument: _Instrument) -> list[Position] | list[OptionPosition]:
    model = OptionPosition if instrument.is_option else Position
    statement = (
        select(model)
        .where(
            model.owner_id == owner_id,
            model.broker_id == trade.broker_id,
            model.side == Side.BUY,
        )
        .order_by(model.id)
    )
    if instrument.is_option:
        assert instrument.contract is not None
        statement = statement.where(OptionPosition.contract_id == instrument.contract.id)
    else:
        statement = statement.where(Position.ticker_id == instrument.ticker.id)
    return list(db.session.scalars(statement))


def _default_portfolio(owner_id: int, currency: str) -> int:
    ids = list(
        db.session.scalars(
            select(Portfolio.id).where(
                Portfolio.owner_id == owner_id,
                Portfolio.is_active.is_(True),
                Portfolio.currency == currency,
            )
        )
    )
    if len(ids) != 1:
        raise _PendingError(
            f"não há uma única carteira ativa em {currency} para abrir a posição ({len(ids)} encontradas)"
        )
    return ids[0]


def _transaction_filter(instrument: _Instrument) -> object:
    if instrument.is_option:
        assert instrument.contract is not None
        return Transaction.option_contract_id == instrument.contract.id
    return Transaction.ticker_id == instrument.ticker.id


def _already_entered(owner_id: int, trade: Trade, instrument: _Instrument) -> str | None:
    """O CRV já tem este negócio? Devolve a evidência, ou ``None``."""
    base = (
        Transaction.owner_id == owner_id,
        Transaction.broker_id == trade.broker_id,
        Transaction.side == Side.BUY,
        _transaction_filter(instrument),
    )
    if trade.is_buy:
        movement = OptionPositionMovement if instrument.is_option else PositionMovement
        position_model = OptionPosition if instrument.is_option else Position
        parent = movement.option_position_id if instrument.is_option else movement.position_id
        live = db.session.scalar(
            select(func.count())
            .select_from(movement)
            .join(position_model, position_model.id == parent)
            .where(
                movement.owner_id == owner_id,
                position_model.broker_id == trade.broker_id,
                position_model.side == Side.BUY,
                (OptionPosition.contract_id == instrument.contract.id)
                if instrument.is_option and instrument.contract is not None
                else (Position.ticker_id == instrument.ticker.id),
                movement.kind.in_([PositionMovementKind.OPEN, PositionMovementKind.INCREASE]),
                movement.occurred_on == trade.trade_date,
                movement.quantity_delta == trade.quantity,
            )
        )
        if live:
            return "o CRV já tem a compra (movimento do mesmo dia e quantidade)"
        closed_lot = db.session.scalar(
            select(func.count()).select_from(Transaction).where(
                *base, Transaction.opened_on == trade.trade_date, Transaction.quantity == trade.quantity
            )
        )
        if closed_lot:
            return "o CRV já tem a compra (lote aberto no mesmo dia e quantidade)"
        return None
    closed = db.session.scalar(
        select(func.coalesce(func.sum(Transaction.quantity), 0)).where(
            *base, Transaction.status == TransactionStatus.CLOSED, Transaction.closed_on == trade.trade_date
        )
    )
    if closed and Decimal(closed) == trade.quantity:
        return "o CRV já tem a venda (lotes fechados no mesmo dia somam a quantidade)"
    return None


def _apply_buy(owner_id: int, trade: Trade, instrument: _Instrument) -> str:
    existing = _open_positions(owner_id, trade, instrument)
    if len({position.portfolio_id for position in existing}) > 1:
        raise _PendingError("a posição existe em mais de uma carteira; aplique à mão")
    portfolio_id = existing[0].portfolio_id if existing else _default_portfolio(owner_id, trade.currency)
    if instrument.is_option:
        assert instrument.contract is not None
        option_candidate = OptionPosition(
            owner_id=owner_id, broker_id=trade.broker_id, contract_id=instrument.contract.id,
            quantity=trade.quantity, average_cost=trade.price, target_price=None, side=Side.BUY,
            opened_on=trade.trade_date, portfolio_id=portfolio_id,
        )
        option_position, merged = options.create_or_merge_position(option_candidate)
        position_id = option_position.id
    else:
        candidate = Position(
            owner_id=owner_id, broker_id=trade.broker_id, ticker_id=instrument.ticker.id,
            quantity=trade.quantity, average_cost=trade.price, side=Side.BUY,
            opened_on=trade.trade_date, portfolio_id=portfolio_id,
        )
        # A execução é fato da B3: um negócio anterior à abertura registrada
        # reordena o extrato da posição (é o que a confirmação da tela autoriza).
        position, merged = stocks.create_or_merge_position(candidate, confirm_prior_opening=True)
        position_id = position.id
    grant_ticker_entitlement(user_id=owner_id, ticker_id=instrument.ticker.id, held_on=trade.trade_date)
    kind = "opção" if instrument.is_option else "posição"
    return f"{'reforçou' if merged else 'abriu'} a {kind} {position_id} a {trade.price:.4f}"


def _apply_sell(owner_id: int, trade: Trade, instrument: _Instrument) -> str:
    existing = _open_positions(owner_id, trade, instrument)
    if not existing:
        raise _PendingError("venda sem posição comprada no CRV (lançamento de opção ou venda a descoberto)")
    if len(existing) > 1:
        raise _PendingError("há mais de uma posição comprada (carteiras diferentes); feche à mão")
    position = existing[0]
    if position.quantity < trade.quantity:
        raise _PendingError(
            f"a venda de {trade.quantity.normalize():f} passa da posição de {position.quantity.normalize():f} no CRV"
        )
    closer = options.apply_position_closure if instrument.is_option else stocks.apply_position_closure
    total = position.quantity == trade.quantity
    transaction = closer(position.id, trade.price, trade.trade_date, trade.quantity, owner_id=owner_id)
    if transaction is None:
        raise _PendingError("a posição deixou de existir durante a aplicação")
    return f"{'fechou' if total else 'reduziu'} a posição {position.id} a {trade.price:.4f}"


def pending_executions(owner_id: int, *, until: date | None = None) -> list[Execution]:
    statement = select(Execution).where(
        Execution.owner_id == owner_id,
        Execution.in_scope.is_(True),
        Execution.applied_at.is_(None),
    )
    if until is not None:
        statement = statement.where(Execution.trade_date <= until)
    return list(db.session.scalars(statement.order_by(Execution.trade_date, Execution.document, Execution.line_order)))


def _mark(execution_ids: Iterable[int], application: str, note: str, moment: datetime) -> None:
    for execution in db.session.scalars(select(Execution).where(Execution.id.in_(list(execution_ids)))):
        execution.applied_at = moment
        execution.application = application
        execution.application_note = note[:300]


def apply_executions(owner_id: int, *, until: date | None = None, now: datetime | None = None) -> ApplicationReport:
    """Aplica as execuções pendentes, sem ``commit``: a transação é de quem chama.

    O comando faz ``commit`` na gravação e ``rollback`` no ensaio, que assim
    mostra exatamente o que a gravação faria. Cada negócio roda num ponto de
    salvamento: a pendência desfaz só o dele.
    """
    moment = now or datetime.now(UTC)
    report = ApplicationReport()
    for trade in group_trades(pending_executions(owner_id, until=until)):
        savepoint = db.session.begin_nested()
        try:
            instrument = _instrument(trade)
            evidence = _already_entered(owner_id, trade, instrument)
            if evidence is not None:
                _mark(trade.execution_ids, ALREADY_ENTERED, evidence, moment)
                outcome = Outcome(trade, ALREADY_ENTERED, evidence)
            else:
                detail = (_apply_buy if trade.is_buy else _apply_sell)(owner_id, trade, instrument)
                _mark(trade.execution_ids, APPLIED, detail, moment)
                outcome = Outcome(trade, APPLIED, detail)
            db.session.flush()
            savepoint.commit()
        except (_PendingError, ValueError) as exc:
            savepoint.rollback()
            outcome = Outcome(trade, PENDING, str(exc))
        report.outcomes.append(outcome)
    return report


def mark_baseline(
    owner_id: int, *, until: date, note: str, asset: str | None = None, now: datetime | None = None
) -> int:
    """Marca como linha de base as execuções pendentes até ``until`` (sem commit).

    Para a adoção (o que já foi conferido pela conciliação) e para o negócio que
    o mantenedor resolveu à mão (``asset``), como o exercício de opção.
    """
    executions = pending_executions(owner_id, until=until)
    if asset is not None:
        executions = [execution for execution in executions if execution.asset.upper() == asset.upper()]
    _mark((execution.id for execution in executions), BASELINE, note, now or datetime.now(UTC))
    db.session.flush()
    return len(executions)
