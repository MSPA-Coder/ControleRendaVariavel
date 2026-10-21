"""Conciliação mensal: o que o myProfit (que lê a B3) tem contra o que o CRV tem.

Só leitura sobre as posições e as transações. Duas comparações, ambas por ativo
da B3 (ações, opções e ETF; futuros, Tesouro e exterior ficam de fora):

* **posição**: quantidade e custo médio da "Posição detalhada" mais recente
  contra as posições do CRV **na mesma data** (``crv_holdings``), somadas por
  ativo (o relatório não traz corretora);
* **venda**: soma das quantidades vendidas nas execuções do período contra a
  soma das quantidades fechadas nas transações do CRV no mesmo período. É o que
  acusa o ciclo inteiro que ficou de fora (compra e venda dentro do período, sem
  posição aberta para a comparação de posição enxergar).

A diferença que o mantenedor explicou fica registrada em ``ReconciliationNote``
e passa a aparecer como "reconhecida", sem contar como pendência. O custo médio
que só difere pelas taxas de compra (o myProfit as soma ao preço, o CRV não) é
explicado pelas próprias execuções e aparece como "taxas", sem nota manual.

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
    OptionPositionMovement,
    Portfolio,
    Position,
    PositionMovement,
    PositionMovementKind,
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
#: Custo médio que só difere pelas taxas: o myProfit as soma ao preço, o CRV
#: não (convenção da planilha). Explicado pelas execuções, não é pendência.
FEES = "taxas"

STATE_LABELS = {
    MATCH: "Confere",
    QUANTITY: "Quantidade",
    AVERAGE: "Custo médio",
    ONLY_MYPROFIT: "Só no myProfit",
    ONLY_CRV: "Só no CRV",
    ONLY_EXECUTIONS: "Só nas execuções",
    ACKNOWLEDGED: "Reconhecida",
    FEES: "Taxas",
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
        return self.state not in {MATCH, ACKNOWLEDGED, FEES}


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


@dataclass(frozen=True, slots=True)
class FeeLot:
    """Uma execução do ativo, para reconstituir as taxas do lote em carteira."""

    trade_date: date
    order: tuple[str, int]
    quantity: Decimal  # com sinal: compra positiva, venda negativa
    fees: Decimal


def fees_in_lot(lots: Iterable[FeeLot]) -> tuple[Decimal, Decimal]:
    """Quantidade em carteira e taxas de compra embutidas nela, pelas execuções.

    Custo médio: a compra soma quantidade e taxa; a venda tira a quantidade e a
    taxa na proporção (as taxas por unidade do saldo não mudam); zerar a
    posição zera as taxas. Devolve ``(quantidade, taxas)``.
    """
    quantity = Decimal(0)
    fees = Decimal(0)
    for lot in sorted(lots, key=lambda item: (item.trade_date, item.order)):
        if lot.quantity > 0:
            quantity += lot.quantity
            fees += lot.fees
        elif quantity > 0:
            remaining = max(quantity + lot.quantity, Decimal(0))
            fees = fees * remaining / quantity
            quantity = remaining
    return quantity, fees


def explain_by_fees(findings: list[Finding], lots_by_asset: Mapping[str, list[FeeLot]]) -> list[Finding]:
    """Custo médio diferente que as taxas das execuções explicam vira ``taxas``.

    Só quando o histórico de execuções reproduz a quantidade do CRV (senão faltam
    compras e a explicação seria chute) e o custo do CRV mais as taxas por
    unidade bate com o do myProfit, na mesma tolerância.
    """
    result = []
    for finding in findings:
        theirs, ours = finding.myprofit, finding.crv
        lots = lots_by_asset.get(finding.asset)
        if finding.state == AVERAGE and theirs and ours and ours.average is not None and lots:
            quantity, fees = fees_in_lot(lots)
            if quantity == ours.quantity and quantity > 0:
                per_unit = fees / quantity
                if _same_average(theirs.average, ours.average + per_unit):
                    finding = Finding(
                        finding.kind, finding.asset, FEES, theirs, ours,
                        f"taxas de compra de {per_unit:.4f} por unidade (o CRV guarda o preço sem taxas)",
                    )
        result.append(finding)
    return result


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


#: Movimentos que são negócio com data; o ajuste é correção de cadastro e vale
#: para trás (corrigir hoje um lote digitado errado não muda a data dele).
_TRADE_KINDS = frozenset({PositionMovementKind.OPEN, PositionMovementKind.INCREASE, PositionMovementKind.DECREASE})


@dataclass(frozen=True, slots=True)
class MovementRow:
    occurred_on: date
    id: int
    kind: PositionMovementKind
    quantity_delta: Decimal
    resulting_average_cost: Decimal


def state_on(current: Holding, movements: Iterable[MovementRow], as_of: date) -> tuple[bool, Holding | None]:
    """Estado de uma posição viva ao fim de ``as_of``: ``(mudou_depois, estado)``.

    A quantidade é a de hoje menos os negócios posteriores à data. O custo médio
    só muda com compra (venda sai pelo custo médio): havendo compra depois da
    data, vale o custo do último movimento até ela. Sem negócio depois da data,
    ``(False, current)``.
    """
    ordered = sorted(movements, key=lambda item: (item.occurred_on, item.id))
    later = [item for item in ordered if item.occurred_on > as_of and item.kind in _TRADE_KINDS]
    if not later:
        return False, current
    quantity = current.quantity - sum((item.quantity_delta for item in later), Decimal(0))
    if quantity <= 0:
        return True, None
    average = current.average
    if any(item.kind != PositionMovementKind.DECREASE for item in later):
        before = [item for item in ordered if item.occurred_on <= as_of]
        average = before[-1].resulting_average_cost if before else None
    return True, Holding(quantity, average)


def crv_holdings(owner_id: int, as_of: date | None = None, *, b3: bool = True) -> dict[str, Holding]:
    """Posições do CRV por ativo (ações e opções); com ``b3=False``, as do exterior.

    Sem ``as_of``, as abertas hoje. Com ``as_of``, as que existiam ao fim daquele
    dia, para comparar com a posição do myProfit **da mesma data**:

    * posição viva sem negócio depois da data e aberta até ela: vale como está;
    * posição viva com negócio depois da data (compra ou venda parcial): volta ao
      estado da data (``state_on``);
    * lote fechado depois da data e aberto até ela (``transactions``): volta a
      contar, salvo se a posição de origem ainda existe e já foi recontada pelo
      movimento (é a venda parcial do item anterior).

    Comparar a posição de hoje com o myProfit de uma data passada acusa como
    "só no myProfit" o que foi vendido depois, e a correção ingênua reabre o
    papel vendido.
    """
    mercado = Ticker.market == Market.B3 if b3 else Ticker.market != Market.B3
    lots: dict[str, list[Holding]] = defaultdict(list)
    stock_rows = db.session.execute(
        select(Position.id, Ticker.symbol, Position.quantity, Position.average_cost, Position.opened_on)
        .join(Ticker, Ticker.id == Position.ticker_id)
        .where(Position.owner_id == owner_id, Position.side == Side.BUY, mercado)
    ).all()
    option_rows = db.session.execute(
        select(OptionPosition.id, Ticker.symbol, OptionPosition.quantity, OptionPosition.average_cost, OptionPosition.opened_on)
        .join(OptionContract, OptionContract.id == OptionPosition.contract_id)
        .join(Ticker, Ticker.id == OptionContract.ticker_id)
        .where(
            OptionPosition.owner_id == owner_id,
            OptionPosition.side == Side.BUY,
            mercado,
        )
    ).all()
    if as_of is None:
        for _, symbol, quantity, average, _ in (*stock_rows, *option_rows):
            lots[symbol].append(Holding(quantity, average))
        return {asset: combined for asset, rows in lots.items() if (combined := combine(rows))}

    #: (é opção?, id da posição) das posições vivas recontadas pelo movimento.
    rolled_back: set[tuple[bool, int]] = set()
    for is_option, rows, movement in (
        (False, stock_rows, PositionMovement),
        (True, option_rows, OptionPositionMovement),
    ):
        owner_column = movement.option_position_id if is_option else movement.position_id
        history: dict[int, list[MovementRow]] = defaultdict(list)
        if rows:
            for position_id, occurred_on, movement_id, kind, delta, average in db.session.execute(
                select(
                    owner_column, movement.occurred_on, movement.id, movement.kind,
                    movement.quantity_delta, movement.resulting_average_cost,
                ).where(owner_column.in_([row[0] for row in rows]))
            ):
                history[position_id].append(MovementRow(occurred_on, movement_id, kind, delta, average))
        for position_id, symbol, quantity, average, opened_on in rows:
            changed, state = state_on(Holding(quantity, average), history.get(position_id, ()), as_of)
            if changed:
                rolled_back.add((is_option, position_id))
            elif opened_on > as_of:
                continue
            if state is not None:
                lots[symbol].append(state)

    closed_base = (
        Transaction.owner_id == owner_id,
        Transaction.status == TransactionStatus.CLOSED,
        Transaction.side == Side.BUY,
        Transaction.opened_on <= as_of,
        Transaction.closed_on > as_of,
        mercado,
    )
    for is_option, statement in (
        (
            False,
            select(Ticker.symbol, Transaction.quantity, Transaction.average_cost, Transaction.source_position_id)
            .join(Ticker, Ticker.id == Transaction.ticker_id),
        ),
        (
            True,
            select(Ticker.symbol, Transaction.quantity, Transaction.average_cost, Transaction.source_position_id)
            .join(OptionContract, OptionContract.id == Transaction.option_contract_id)
            .join(Ticker, Ticker.id == OptionContract.ticker_id),
        ),
    ):
        for symbol, quantity, average, source_id in db.session.execute(
            statement.join(Portfolio, Portfolio.id == Transaction.portfolio_id).where(*closed_base)
        ):
            if source_id is not None and (is_option, source_id) in rolled_back:
                continue
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


def execution_lots(owner_id: int, as_of: date) -> dict[str, list[FeeLot]]:
    """Execuções em escopo até a data, por ativo, para ``explain_by_fees``."""
    lots: dict[str, list[FeeLot]] = defaultdict(list)
    for execution in db.session.scalars(
        select(Execution).where(
            Execution.owner_id == owner_id,
            Execution.in_scope.is_(True),
            Execution.trade_date <= as_of,
        )
    ):
        lots[execution.asset].append(
            FeeLot(execution.trade_date, (execution.document, execution.line_order), execution.quantity, execution.fees_total)
        )
    return dict(lots)


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
    positions = (
        explain_by_fees(
            compare_positions(theirs, crv_holdings(owner_id, snapshot_date)),
            execution_lots(owner_id, snapshot_date),
        )
        if snapshot_date
        else []
    )
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


#: Grupos do exterior que o CRV também tem como posição (a renda fixa EUA e a conta
#: corrente em dólar moram no CB).
EXTERIOR_GROUPS = frozenset({"acao_eua", "etf_usa"})


def myprofit_exterior(owner_id: int, as_of: date) -> dict[str, Holding]:
    """Quantidade por ativo do exterior na posição do myProfit da data.

    Só quantidade: o relatório traz o custo do exterior em reais, convertido por
    uma taxa do myProfit, e o CRV guarda em dólar; comparar os dois acusaria
    câmbio, não lote errado.
    """
    from app.executions.myprofit import ASSET_GROUPS, _fold

    totals: dict[str, Decimal] = defaultdict(Decimal)
    for asset, group_label, quantity in db.session.execute(
        select(MyprofitPosition.asset, MyprofitPosition.group_label, MyprofitPosition.quantity).where(
            MyprofitPosition.owner_id == owner_id, MyprofitPosition.as_of == as_of
        )
    ):
        if ASSET_GROUPS.get(_fold(group_label)) in EXTERIOR_GROUPS:
            totals[asset] += quantity
    return {asset: Holding(total) for asset, total in totals.items()}


def build_exterior(owner_id: int, as_of: date) -> list[Finding]:
    """Ações e ETF do exterior: o myProfit da data contra o CRV da mesma data.

    Quem escolhe ``as_of`` leva em conta o atraso da integração da Avenue no
    myProfit (dias): a posição de uma data recente ainda não tem os últimos
    negócios. Diferença explicada (o SGOV da Nomad que o myProfit não lê, por
    exemplo) fica reconhecida pela mesma nota de posição da B3.
    """
    def lado(holdings: Mapping[str, Holding]) -> dict[str, Holding]:
        # O mesmo papel tem dois nomes: o CRV grava "BRK.B" e o myProfit "BRKB".
        # A comparação usa o código sem ponto, e é por ele que se reconhece.
        totais: dict[str, Decimal] = defaultdict(Decimal)
        for asset, holding in holdings.items():
            totais[_codigo_do_exterior(asset)] += holding.quantity
        return {asset: Holding(total) for asset, total in totais.items()}

    theirs = lado(myprofit_exterior(owner_id, as_of))
    ours = lado(crv_holdings(owner_id, as_of, b3=False))
    return apply_notes(compare_positions(theirs, ours), notes_of(owner_id))


def _codigo_do_exterior(asset: str) -> str:
    return asset.replace(".", "").replace("/", "").upper()


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
