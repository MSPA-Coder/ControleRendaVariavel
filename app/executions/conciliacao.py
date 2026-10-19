"""Conciliação mensal: o que o myProfit (que lê a B3) tem contra o que o CRV tem.

Só leitura sobre as posições e as transações. Duas comparações, ambas por ativo
da B3 (ações, opções e ETF; futuros, Tesouro e exterior ficam de fora):

* **posição**: quantidade e custo médio da "Posição detalhada" mais recente
  contra as posições abertas do CRV, somadas por ativo (o relatório não traz
  corretora) e sem a carteira Simulada;
* **venda**: soma das quantidades vendidas nas execuções do período contra a
  soma das quantidades fechadas nas transações do CRV no mesmo período. É o que
  acusa o ciclo inteiro que ficou de fora (compra e venda dentro do período, sem
  posição aberta para a comparação de posição enxergar).

A diferença que o mantenedor explicou fica registrada em ``ReconciliationNote``
e passa a aparecer como "reconhecida", sem contar como pendência.

``compare_positions`` e ``compare_sales`` são puras; ``build_conciliation`` é a
ligação com o banco.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app import db
from app.core.domain import Side
from app.executions.posicao_myprofit import PositionReport
from app.models import (
    Execution,
    Market,
    MyprofitPosition,
    OptionContract,
    OptionPosition,
    Portfolio,
    Position,
    ReconciliationNote,
    Ticker,
    Transaction,
    TransactionStatus,
)

QUANTITY_TOLERANCE = Decimal("0.00000001")
#: Custo médio: o myProfit guarda quatro casas e o CRV oito, e a média ponderada
#: de lotes acumula arredondamento; um décimo por cento absorve isso sem
#: esconder um lote errado.
AVERAGE_TOLERANCE = Decimal("0.001")

MATCH = "confere"
QUANTITY = "quantidade"
AVERAGE = "custo_medio"
ONLY_MYPROFIT = "so_myprofit"
ONLY_CRV = "so_crv"
ONLY_EXECUTIONS = "so_execucoes"
ACKNOWLEDGED = "reconhecida"

STATE_LABELS = {
    MATCH: "Confere",
    QUANTITY: "Quantidade",
    AVERAGE: "Custo médio",
    ONLY_MYPROFIT: "Só no myProfit",
    ONLY_CRV: "Só no CRV",
    ONLY_EXECUTIONS: "Só nas execuções",
    ACKNOWLEDGED: "Reconhecida",
}

KIND_POSITION = "posicao"
KIND_SALE = "venda"


@dataclass(frozen=True, slots=True)
class Holding:
    """Quantidade e custo médio de um ativo, de um dos dois lados."""

    quantity: Decimal
    average: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    asset: str
    state: str
    myprofit: Holding | None
    crv: Holding | None
    note: str | None = None

    @property
    def pending(self) -> bool:
        return self.state not in {MATCH, ACKNOWLEDGED}


def combine(lots: Iterable[Holding]) -> Holding | None:
    """Soma lotes do mesmo ativo; o custo médio sai ponderado pela quantidade."""
    total = Decimal(0)
    cost = Decimal(0)
    count = 0
    for lot in lots:
        count += 1
        total += lot.quantity
        cost += lot.quantity * (lot.average or Decimal(0))
    if not count:
        return None
    return Holding(total, cost / total if total else None)


def _same_average(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return True
    reference = max(abs(left), abs(right))
    return reference == 0 or abs(left - right) <= reference * AVERAGE_TOLERANCE


def compare_positions(
    myprofit: Mapping[str, Holding], crv: Mapping[str, Holding]
) -> list[Finding]:
    findings = []
    for asset in sorted(myprofit.keys() | crv.keys()):
        theirs, ours = myprofit.get(asset), crv.get(asset)
        if theirs is None:
            state = ONLY_CRV
        elif ours is None:
            state = ONLY_MYPROFIT
        elif abs(theirs.quantity - ours.quantity) > QUANTITY_TOLERANCE:
            state = QUANTITY
        elif not _same_average(theirs.average, ours.average):
            state = AVERAGE
        else:
            state = MATCH
        findings.append(Finding(KIND_POSITION, asset, state, theirs, ours))
    return findings


def compare_sales(executions: Mapping[str, Decimal], closed: Mapping[str, Decimal]) -> list[Finding]:
    findings = []
    for asset in sorted(executions.keys() | closed.keys()):
        theirs, ours = executions.get(asset), closed.get(asset)
        if theirs is None:
            state = ONLY_CRV
        elif ours is None:
            state = ONLY_EXECUTIONS
        elif abs(theirs - ours) > QUANTITY_TOLERANCE:
            state = QUANTITY
        else:
            state = MATCH
        findings.append(
            Finding(
                KIND_SALE,
                asset,
                state,
                Holding(theirs) if theirs is not None else None,
                Holding(ours) if ours is not None else None,
            )
        )
    return findings


def apply_notes(findings: list[Finding], notes: Mapping[tuple[str, str], str]) -> list[Finding]:
    """Marca como reconhecida a diferença que tem nota; o que confere fica como está."""
    result = []
    for finding in findings:
        note = notes.get((finding.kind, finding.asset))
        if note is not None and finding.pending:
            finding = Finding(
                finding.kind, finding.asset, ACKNOWLEDGED, finding.myprofit, finding.crv, note
            )
        result.append(finding)
    return result


@dataclass(frozen=True, slots=True)
class Conciliation:
    as_of: date | None
    start: date
    end: date
    positions: list[Finding]
    sales: list[Finding]

    @property
    def findings(self) -> list[Finding]:
        return [*self.positions, *self.sales]

    @property
    def pending(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.pending]


def myprofit_holdings(owner_id: int, as_of: date | None = None) -> tuple[date | None, dict[str, Holding]]:
    """Posição do myProfit na data pedida, ou na mais recente gravada."""
    if as_of is None:
        as_of = db.session.scalar(
            select(func.max(MyprofitPosition.as_of)).where(MyprofitPosition.owner_id == owner_id)
        )
    if as_of is None:
        return None, {}
    lots: dict[str, list[Holding]] = defaultdict(list)
    for row in db.session.scalars(
        select(MyprofitPosition)
        .where(
            MyprofitPosition.owner_id == owner_id,
            MyprofitPosition.as_of == as_of,
            MyprofitPosition.in_scope.is_(True),
        )
        .order_by(MyprofitPosition.line_order)
    ):
        lots[row.asset].append(Holding(row.quantity, row.average_price))
    return as_of, {asset: combined for asset, rows in lots.items() if (combined := combine(rows))}


def crv_holdings(owner_id: int) -> dict[str, Holding]:
    """Posições abertas do CRV por ativo (ações e opções), sem a carteira Simulada."""
    lots: dict[str, list[Holding]] = defaultdict(list)
    for symbol, quantity, average in db.session.execute(
        select(Ticker.symbol, Position.quantity, Position.average_cost)
        .join(Ticker, Ticker.id == Position.ticker_id)
        .join(Portfolio, Portfolio.id == Position.portfolio_id)
        .where(Position.owner_id == owner_id, Portfolio.simulated.is_(False), Position.side == Side.BUY, Ticker.market == Market.B3)
    ):
        lots[symbol].append(Holding(quantity, average))
    for symbol, quantity, average in db.session.execute(
        select(Ticker.symbol, OptionPosition.quantity, OptionPosition.average_cost)
        .join(OptionContract, OptionContract.id == OptionPosition.contract_id)
        .join(Ticker, Ticker.id == OptionContract.ticker_id)
        .join(Portfolio, Portfolio.id == OptionPosition.portfolio_id)
        .where(
            OptionPosition.owner_id == owner_id,
            Portfolio.simulated.is_(False),
            OptionPosition.side == Side.BUY,
            Ticker.market == Market.B3,
        )
    ):
        lots[symbol].append(Holding(quantity, average))
    return {asset: combined for asset, rows in lots.items() if (combined := combine(rows))}


def execution_sales(owner_id: int, start: date, end: date) -> dict[str, Decimal]:
    """Quantidade vendida por ativo nas execuções em escopo do período."""
    return {
        asset: -total
        for asset, total in db.session.execute(
            select(Execution.asset, func.sum(Execution.quantity))
            .where(
                Execution.owner_id == owner_id,
                Execution.in_scope.is_(True),
                Execution.side == "V",
                Execution.trade_date >= start,
                Execution.trade_date <= end,
            )
            .group_by(Execution.asset)
        )
    }


def crv_closed_quantities(owner_id: int, start: date, end: date) -> dict[str, Decimal]:
    """Quantidade fechada por ativo nas transações do CRV no período (compradas)."""
    base = (
        Transaction.owner_id == owner_id,
        Transaction.status == TransactionStatus.CLOSED,
        Transaction.side == Side.BUY,
        Transaction.closed_on >= start,
        Transaction.closed_on <= end,
        Portfolio.simulated.is_(False),
        Ticker.market == Market.B3,
    )
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for symbol, total in db.session.execute(
        select(Ticker.symbol, func.sum(Transaction.quantity))
        .join(Ticker, Ticker.id == Transaction.ticker_id)
        .join(Portfolio, Portfolio.id == Transaction.portfolio_id)
        .where(*base)
        .group_by(Ticker.symbol)
    ):
        totals[symbol] += total
    for symbol, total in db.session.execute(
        select(Ticker.symbol, func.sum(Transaction.quantity))
        .join(OptionContract, OptionContract.id == Transaction.option_contract_id)
        .join(Ticker, Ticker.id == OptionContract.ticker_id)
        .join(Portfolio, Portfolio.id == Transaction.portfolio_id)
        .where(*base)
        .group_by(Ticker.symbol)
    ):
        totals[symbol] += total
    return dict(totals)


def notes_of(owner_id: int) -> dict[tuple[str, str], str]:
    return {
        (note.kind, note.asset): note.note
        for note in db.session.scalars(
            select(ReconciliationNote).where(ReconciliationNote.owner_id == owner_id)
        )
    }


def build_conciliation(
    owner_id: int, *, start: date, end: date, as_of: date | None = None
) -> Conciliation:
    snapshot_date, theirs = myprofit_holdings(owner_id, as_of)
    positions = compare_positions(theirs, crv_holdings(owner_id)) if snapshot_date else []
    sales = compare_sales(
        execution_sales(owner_id, start, end), crv_closed_quantities(owner_id, start, end)
    )
    notes = notes_of(owner_id)
    return Conciliation(
        as_of=snapshot_date,
        start=start,
        end=end,
        positions=apply_notes(positions, notes),
        sales=apply_notes(sales, notes),
    )


def save_position_snapshot(owner_id: int, report: PositionReport) -> tuple[int, int]:
    """Grava a posição da data do relatório, substituindo a que já existia nela.

    Devolve ``(removidas, gravadas)``. Não faz commit: quem chama decide.
    """
    removed = (
        db.session.query(MyprofitPosition)
        .filter(MyprofitPosition.owner_id == owner_id, MyprofitPosition.as_of == report.as_of)
        .delete(synchronize_session=False)
    )
    for order, row in enumerate(report.rows, start=1):
        db.session.add(
            MyprofitPosition(
                owner_id=owner_id,
                as_of=report.as_of,
                line_order=order,
                asset=row.asset,
                group_label=row.group_label,
                in_scope=row.in_scope,
                quantity=row.quantity,
                average_price=row.average_price,
                total_invested=row.total_invested,
                sha256=report.sha256,
            )
        )
    db.session.flush()
    return removed, len(report.rows)


def set_note(owner_id: int, kind: str, asset: str, note: str) -> None:
    existing = db.session.scalar(
        select(ReconciliationNote).where(
            ReconciliationNote.owner_id == owner_id,
            ReconciliationNote.kind == kind,
            ReconciliationNote.asset == asset,
        )
    )
    if existing is None:
        db.session.add(ReconciliationNote(owner_id=owner_id, kind=kind, asset=asset, note=note))
    else:
        existing.note = note
    db.session.flush()


def remove_note(owner_id: int, kind: str, asset: str) -> bool:
    removed = (
        db.session.query(ReconciliationNote)
        .filter(
            ReconciliationNote.owner_id == owner_id,
            ReconciliationNote.kind == kind,
            ReconciliationNote.asset == asset,
        )
        .delete(synchronize_session=False)
    )
    return bool(removed)
