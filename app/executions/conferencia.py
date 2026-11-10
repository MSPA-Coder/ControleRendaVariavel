"""Conferência por pregão: liquidação do CB contra os negócios do CRV.

Só leitura. Nenhum lado grava no outro: a diferença é mostrada, e ela é o
**custo implícito** do pregão — o que a corretora e a liquidação levaram além
das taxas da B3 que o myProfit já contabiliza (o CRV não guarda custos).

Na B3 a comparação é por corretora, data do pregão e moeda:

* CB: soma assinada dos lançamentos "Operações em Bolsa" da(s) conta(s) da
  corretora (venda positiva, compra negativa);
* CRV: soma de ``total_with_fees`` das execuções **em escopo** (ações, opções e
  ETF da B3), que já vem com o sinal do caixa.

No exterior (``ledger_brokers``) o myProfit não traz os negócios, mas o extrato
traz cada um com ativo e quantidade. A comparação é negócio a negócio --
corretora, pregão, moeda e ativo -- contra o extrato de posição do CRV: a
quantidade sempre, e o valor quando o CRV tem o preço (``ledger_trades``).

``compare`` e ``compare_trades`` são puras; ``build_reconciliation`` é a
ligação com o banco e o CB.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import current_app
from sqlalchemy import select

from app import db
from app.core.currency_filter import ALL
from app.executions.carteira import HoldingCheck, holdings_rows, symbol_key
from app.models import (
    Broker,
    Execution,
    Position,
    PositionMovement,
    PositionMovementArchive,
    PositionMovementKind,
    Side,
    Ticker,
)
from app.patrimonio.cb_client import (
    SETTLEMENT_CATEGORY,
    CbConfig,
    CbSettlement,
    CbUnavailableError,
    Fetcher,
    parse_config,
    read_settlements,
)
from app.patrimonio.queries import position_timeline
from app.quotes.symbols import current_symbol, former_symbol_map

#: Cobre uma corretagem de 5,00 mais o arredondamento do preço em quatro casas. A
#: da Avenue (US$ 5 por ordem) está dentro do preço do CRV e à parte no CB, e o
#: arredondamento soma centavos ao total (até 0,25 em 5 mil ações): em 09 e
#: 20/10/2026 dois pregões de CGC deram 5,01 e 5,07 e reprovavam com 5,00.
DEFAULT_TOLERANCE = Decimal("5.25")

#: Corretoras cujos negócios o myProfit não traz (``Execution.in_scope`` é só a
#: B3): o lado do CRV é o extrato de posição, negócio a negócio. Nomad e Revolut
#: ficam de fora: são dinheiro de viagem e não têm liquidação no CB
#: (CONCEITOS_FINANCEIROS, regra 10). ``CONFERENCIA_CORRETORAS_EXTERIOR`` (nomes
#: no CRV, separados por vírgula) substitui a lista.
DEFAULT_LEDGER_BROKERS: tuple[str, ...] = ("Avenue",)

#: Corretora do CRV -> identidades das instituições do CB (``institution`` no
#: contrato v4) cujas contas recebem a liquidação dos pregões dela. Em produção
#: o CB tem: Genial = conta 8; XP = SCP XP Investimentos (conta 10); Nomad = 13;
#: Avenue = 12 (USD) e 26 (BRL); Revolut = 14. A tabela é por instituição, e não
#: por id, porque o id do CB é opaco no contrato. ``CB_CORRESPONDENCIA_CONTAS``
#: (JSON no ambiente) a substitui por inteiro.
DEFAULT_BROKER_INSTITUTIONS: dict[str, tuple[str, ...]] = {
    "Genial": ("genial",),
    "XP": ("scp-xp-investimentos",),
    "Nomad": ("nomad",),
    "Avenue": ("avenue",),
    "Revolut": ("revolut",),
}

MATCH = "confere"
DIFFERENCE = "diferenca"
ONLY_CB = "so_cb"
ONLY_CRV = "so_crv"
NO_CB = "sem_cb"
#: Só no CRV, mas o extrato da corretora no CB ainda não chega à liquidação: falta
#: importar o extrato, não falta dinheiro.
AWAITING_STATEMENT = "aguardando_extrato"

#: A liquidação sai em D+1 ou D+2 úteis; cinco dias corridos cobrem o fim de
#: semana e um feriado. Pregão cuja liquidação esperada passa do último dia do
#: extrato importado aguarda o extrato.
SETTLEMENT_MARGIN = timedelta(days=5)

STATE_LABELS = {
    MATCH: "Confere",
    DIFFERENCE: "Diferença",
    ONLY_CB: "Só no CB",
    ONLY_CRV: "Só no CRV",
    NO_CB: "Sem comparação",
    AWAITING_STATEMENT: "Aguardando extrato",
}


@dataclass(frozen=True, slots=True)
class Movement:
    """Um lado da comparação: valor assinado de uma corretora num pregão."""

    broker: str
    pregao: date
    currency: str
    amount: Decimal
    label: str = ""


@dataclass(frozen=True, slots=True)
class ReconciliationRow:
    broker: str
    pregao: date
    currency: str
    state: str
    cb_amount: Decimal | None
    crv_amount: Decimal | None
    #: CRV - CB: positivo quando a liquidação levou mais do que o myProfit contou.
    implicit_cost: Decimal | None
    documents: tuple[str, ...] = ()
    #: Só no exterior, onde a comparação é negócio a negócio: o ativo e a
    #: quantidade de cada lado, com o sinal da posição (compra positiva).
    instrument: str = ""
    cb_quantity: Decimal | None = None
    crv_quantity: Decimal | None = None

    @property
    def state_label(self) -> str:
        return STATE_LABELS[self.state]


@dataclass(frozen=True, slots=True)
class Trade:
    """Um negócio no exterior, de um dos lados: ativo, quantidade e caixa.

    ``quantity`` tem o sinal da posição (compra positiva); ``amount``, o do caixa
    (compra negativa). ``amount`` é ``None`` quando o CRV só sabe a quantidade:
    posição encerrada antes de o extrato de movimentos ser arquivado.
    """

    broker: str
    pregao: date
    currency: str
    instrument: str
    quantity: Decimal | None
    amount: Decimal | None


def _sum_or_none(values: Iterable[Decimal | None]) -> Decimal | None:
    total = Decimal("0")
    for value in values:
        if value is None:
            return None
        total += value
    return total


def compare_trades(
    cb: Iterable[Trade],
    crv: Iterable[Trade],
    *,
    tolerance: Decimal = DEFAULT_TOLERANCE,
    cb_available: bool = True,
    coverage: Mapping[tuple[str, str], date] | None = None,
) -> list[ReconciliationRow]:
    """Classifica cada (corretora, pregão, moeda, ativo) do exterior.

    Confere quando a quantidade é a mesma dos dois lados e, se o CRV tem o
    preço, o valor fica dentro da tolerância. Sem nada a comparar (o CB sem
    quantidade e o CRV sem preço) é diferença: confere é afirmação, não falta de
    dado. Os demais estados são os de ``compare``.
    """
    coverage = coverage or {}

    def group(trades: Iterable[Trade]) -> dict[tuple[str, date, str, str], list[Trade]]:
        grouped: dict[tuple[str, date, str, str], list[Trade]] = defaultdict(list)
        for trade in trades:
            grouped[(trade.broker, trade.pregao, trade.currency, symbol_key(trade.instrument))].append(trade)
        return grouped

    cb_groups, crv_groups = group(cb), group(crv)
    rows: list[ReconciliationRow] = []
    for key in sorted(cb_groups.keys() | crv_groups.keys(), key=lambda k: (k[1], k[0], k[2], k[3])):
        cb_trades, crv_trades = cb_groups.get(key), crv_groups.get(key)
        cb_quantity = _sum_or_none(t.quantity for t in cb_trades) if cb_trades else None
        cb_amount = _sum_or_none(t.amount for t in cb_trades) if cb_trades else None
        crv_quantity = _sum_or_none(t.quantity for t in crv_trades) if crv_trades else None
        crv_amount = _sum_or_none(t.amount for t in crv_trades) if crv_trades else None
        if not cb_available:
            state = NO_CB
        elif cb_trades is None:
            covered = coverage.get((key[0], key[2]))
            state = AWAITING_STATEMENT if covered is not None and key[1] + SETTLEMENT_MARGIN > covered else ONLY_CRV
        elif crv_trades is None:
            state = ONLY_CB
        else:
            checks = []
            if cb_quantity is not None and crv_quantity is not None:
                checks.append(cb_quantity == crv_quantity)
            if cb_amount is not None and crv_amount is not None:
                checks.append(abs(crv_amount - cb_amount) <= tolerance)
            state = MATCH if checks and all(checks) else DIFFERENCE
        instrument = (crv_trades or cb_trades or [])[0].instrument
        rows.append(
            ReconciliationRow(
                broker=key[0],
                pregao=key[1],
                currency=key[2],
                state=state,
                cb_amount=cb_amount,
                crv_amount=crv_amount,
                implicit_cost=crv_amount - cb_amount if cb_amount is not None and crv_amount is not None else None,
                documents=(instrument,) if instrument else (),
                instrument=instrument,
                cb_quantity=cb_quantity,
                crv_quantity=crv_quantity,
            )
        )
    return rows


def _totals(movements: Iterable[Movement]) -> tuple[dict[tuple[str, date, str], Decimal], dict[tuple[str, date, str], set[str]]]:
    sums: dict[tuple[str, date, str], Decimal] = defaultdict(Decimal)
    labels: dict[tuple[str, date, str], set[str]] = defaultdict(set)
    for movement in movements:
        key = (movement.broker, movement.pregao, movement.currency)
        sums[key] += movement.amount
        if movement.label:
            labels[key].add(movement.label)
    return sums, labels


def compare(
    cb: Iterable[Movement],
    crv: Iterable[Movement],
    *,
    tolerance: Decimal = DEFAULT_TOLERANCE,
    cb_available: bool = True,
    coverage: Mapping[tuple[str, str], date] | None = None,
) -> list[ReconciliationRow]:
    """Classifica cada (corretora, pregão, moeda) em confere, diferença, só no
    CB ou só no CRV, em ordem de data. ``cb_available=False`` marca tudo como
    sem comparação em vez de chamar de "só no CRV" o que o CB nem foi perguntado.
    ``coverage`` ((corretora, moeda) -> último dia do extrato importado) troca
    "só no CRV" por "aguardando extrato" quando a liquidação esperada do pregão
    passa desse dia.
    """
    coverage = coverage or {}
    cb_sums, _ = _totals(cb)
    crv_sums, crv_documents = _totals(crv)
    rows: list[ReconciliationRow] = []
    for key in sorted(cb_sums.keys() | crv_sums.keys(), key=lambda k: (k[1], k[0], k[2])):
        cb_amount = cb_sums.get(key)
        crv_amount = crv_sums.get(key)
        if not cb_available:
            state = NO_CB
        elif cb_amount is None:
            covered = coverage.get((key[0], key[2]))
            state = AWAITING_STATEMENT if covered is not None and key[1] + SETTLEMENT_MARGIN > covered else ONLY_CRV
        elif crv_amount is None:
            state = ONLY_CB
        else:
            state = MATCH if abs(crv_amount - cb_amount) <= tolerance else DIFFERENCE
        cost = crv_amount - cb_amount if cb_amount is not None and crv_amount is not None else None
        rows.append(
            ReconciliationRow(
                broker=key[0],
                pregao=key[1],
                currency=key[2],
                state=state,
                cb_amount=cb_amount,
                crv_amount=crv_amount,
                implicit_cost=cost,
                documents=tuple(sorted(crv_documents.get(key, ()))),
            )
        )
    return rows


def broker_institutions(config: Mapping[str, object] | None = None) -> dict[str, tuple[str, ...]]:
    raw = (config if config is not None else current_app.config).get("CB_CORRESPONDENCIA_CONTAS")
    if not raw:
        return dict(DEFAULT_BROKER_INSTITUTIONS)
    try:
        loaded = json.loads(str(raw))
        return {
            str(broker): tuple(str(item) for item in institutions)
            for broker, institutions in loaded.items()
        }
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("CB_CORRESPONDENCIA_CONTAS deve mapear corretora para lista de instituições.") from exc


def ledger_brokers(config: Mapping[str, object] | None = None) -> tuple[str, ...]:
    raw = (config if config is not None else current_app.config).get("CONFERENCIA_CORRETORAS_EXTERIOR")
    names = tuple(part.strip() for part in str(raw or "").split(",") if part.strip())
    return names or DEFAULT_LEDGER_BROKERS


def tolerance_from_config(config: Mapping[str, object] | None = None) -> Decimal:
    raw = (config if config is not None else current_app.config).get("CONFERENCIA_TOLERANCIA")
    if raw in (None, ""):
        return DEFAULT_TOLERANCE
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError("CONFERENCIA_TOLERANCIA deve ser um decimal.") from exc
    if not value.is_finite() or value < 0:
        raise ValueError("CONFERENCIA_TOLERANCIA deve ser um decimal não negativo.")
    return value


def _priced_cash(owner_id: int, broker_ids: Iterable[int]) -> dict[tuple[tuple[str, int], date], Decimal]:
    """Caixa de cada negócio que o CRV tem com preço, por (posição, dia), com o sinal do caixa.

    Vem do extrato vivo das posições e da cópia arquivada no encerramento. O
    ajuste manual não é negócio e fica de fora.
    """
    ids = list(broker_ids)
    cash: dict[tuple[tuple[str, int], date], Decimal] = defaultdict(Decimal)
    live = (
        select(Position.id, Position.side, PositionMovement.occurred_on, PositionMovement.quantity_delta, PositionMovement.price)
        .join(PositionMovement, PositionMovement.position_id == Position.id)
        .where(
            Position.owner_id == owner_id,
            Position.broker_id.in_(ids),
            PositionMovement.kind != PositionMovementKind.ADJUSTMENT,
        )
    )
    for position_id, side, occurred_on, delta, price in db.session.execute(live):
        sign = Decimal("1") if side == Side.BUY else Decimal("-1")
        cash[(("stock", position_id), occurred_on)] -= sign * delta * price
    archived = select(
        PositionMovementArchive.source_position_id,
        PositionMovementArchive.side,
        PositionMovementArchive.occurred_on,
        PositionMovementArchive.quantity_delta,
        PositionMovementArchive.price,
    ).where(
        PositionMovementArchive.owner_id == owner_id,
        PositionMovementArchive.broker_id.in_(ids),
        PositionMovementArchive.kind != "adjustment",
    )
    for position_id, side, occurred_on, delta, price in db.session.execute(archived):
        sign = Decimal("1") if side == "C" else Decimal("-1")
        cash[(("stock", position_id), occurred_on)] -= sign * delta * price
    return cash


def ledger_trades(
    owner_id: int, brokers: Iterable[str], *, start: date | None = None, end: date | None = None
) -> list[Trade]:
    """Os negócios das corretoras pelo extrato de posição do CRV (ações; vivas e encerradas).

    A quantidade é a variação do saldo da posição no dia, lida da mesma linha do
    tempo que o desempenho usa (``position_timeline``), que inclui as posições
    encerradas. O valor vem de ``_priced_cash`` e fica ``None`` quando o CRV não
    guardou o preço daquele dia.
    """
    names = list(brokers)
    broker_names = dict(db.session.execute(select(Broker.id, Broker.name).where(Broker.name.in_(names))).all())
    if not broker_names:
        return []
    tickers = {ticker.id: ticker for ticker in db.session.scalars(select(Ticker))}
    by_position: dict[tuple[str, int], list] = defaultdict(list)
    for event in position_timeline(owner_id, include_options=False):
        if event.broker_id in broker_names:
            by_position[event.position_key].append(event)
    cash = _priced_cash(owner_id, broker_names)
    trades: list[Trade] = []
    for position_key, events in by_position.items():
        closing_by_day: dict[date, Decimal] = {}
        for event in sorted(events, key=lambda item: item.occurred_on):
            closing_by_day[event.occurred_on] = event.resulting_signed_quantity
        ticker = tickers[events[0].ticker_id]
        previous = Decimal("0")
        for day in sorted(closing_by_day):
            delta, previous = closing_by_day[day] - previous, closing_by_day[day]
            if delta == 0 or (start and day < start) or (end and day > end):
                continue
            trades.append(
                Trade(
                    broker=broker_names[events[0].broker_id],
                    pregao=day,
                    currency=ticker.currency,
                    instrument=ticker.symbol,
                    quantity=delta,
                    amount=cash.get((position_key, day)),
                )
            )
    return trades


def cb_config_from_app() -> CbConfig | None:
    return parse_config(
        current_app.config.get("CB_PATRIMONIO_URL"), current_app.config.get("CB_PATRIMONIO_TOKEN")
    )


@dataclass(slots=True)
class Reconciliation:
    rows: list[ReconciliationRow]
    tolerance: Decimal
    #: ``ok``, ``nao_configurado`` ou ``indisponivel``.
    cb_status: str
    cb_message: str = ""
    without_pregao: list[CbSettlement] = field(default_factory=list)
    futures_settlements: int = 0
    executions_out_of_scope: int = 0
    #: A carteira de cada extrato da corretora contra a posição do CRV
    #: (``app.executions.carteira``), lida do mesmo snapshot do CB.
    holdings: list[HoldingCheck] = field(default_factory=list)
    #: O vínculo com o CB é por nome (categoria e instituição), e nome se edita
    #: lá. O que deixou de casar aparece aqui, em vez de virar "só no CRV".
    warnings: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys(STATE_LABELS, 0)
        for row in self.rows:
            counts[row.state] += 1
        return counts


def build_reconciliation(
    owner_id: int,
    *,
    broker: str | None = None,
    currency: str = ALL,
    start: date | None = None,
    end: date | None = None,
    fetcher: Fetcher | None = None,
    config_factory: Callable[[], CbConfig | None] = cb_config_from_app,
) -> Reconciliation:
    """Monta a conferência do dono: execuções do banco contra o CB (só leitura)."""
    tolerance = tolerance_from_config()
    statement = (
        select(Execution, Broker.name)
        .join(Broker, Execution.broker_id == Broker.id)
        .where(Execution.owner_id == owner_id)
        .order_by(Execution.trade_date, Execution.document, Execution.line_order)
    )
    if broker:
        statement = statement.where(Broker.name == broker)
    if currency != ALL:
        statement = statement.where(Execution.currency == currency)
    if start:
        statement = statement.where(Execution.trade_date >= start)
    if end:
        statement = statement.where(Execution.trade_date <= end)
    crv: list[Movement] = []
    out_of_scope = 0
    for execution, broker_name in db.session.execute(statement):
        if not execution.in_scope:
            out_of_scope += 1
            continue
        crv.append(
            Movement(
                broker_name,
                execution.trade_date,
                execution.currency,
                execution.total_with_fees,
                execution.document,
            )
        )

    # O CB começa no corte dos proventos (01/01/2026): negócio do exterior anterior a
    # ele não tem liquidação para conferir. Import local: cb_sync importa este módulo.
    from app.dividends.cb_sync import cutoff_date

    ledger = ledger_brokers()
    ledger_start = max(start, cutoff_date()) if start else cutoff_date()
    crv_trades = ledger_trades(
        owner_id, [name for name in ledger if not broker or name == broker], start=ledger_start, end=end
    )
    if currency != ALL:
        crv_trades = [trade for trade in crv_trades if trade.currency == currency]

    try:
        config = config_factory()
    except CbUnavailableError as exc:
        config, error = None, str(exc)
    else:
        error = ""
    if config is None:
        status, message = (
            ("indisponivel", error) if error else ("nao_configurado", "CB não configurado.")
        )
        return Reconciliation(
            rows=_in_order(
                compare([], crv, tolerance=tolerance, cb_available=False)
                + compare_trades([], crv_trades, tolerance=tolerance, cb_available=False)
            ),
            tolerance=tolerance,
            cb_status=status,
            cb_message=message,
            executions_out_of_scope=out_of_scope,
        )
    try:
        settlements = read_settlements(config, fetcher=fetcher)
    except CbUnavailableError as exc:
        return Reconciliation(
            rows=_in_order(
                compare([], crv, tolerance=tolerance, cb_available=False)
                + compare_trades([], crv_trades, tolerance=tolerance, cb_available=False)
            ),
            tolerance=tolerance,
            cb_status="indisponivel",
            cb_message=str(exc),
            executions_out_of_scope=out_of_scope,
        )

    institution_to_broker: dict[str, str] = {}
    for broker_name, institutions in broker_institutions().items():
        for institution in institutions:
            institution_to_broker[institution] = broker_name
    coverage: dict[tuple[str, str], date] = {}
    # Nome próprio para a moeda da cobertura: ``currency`` é o filtro do pedido.
    for (institution, covered_currency), covered in settlements.coverage.items():
        broker_name = institution_to_broker.get(institution)
        if broker_name is not None:
            key = (broker_name, covered_currency)
            coverage[key] = max(covered, coverage.get(key, covered))
    cb: list[Movement] = []
    cb_trades: list[Trade] = []
    # O extrato traz o código da data dele; o CRV, o vigente.
    former = former_symbol_map()
    for item in settlements.items:
        if currency != ALL and item.currency != currency:
            continue
        broker_name = institution_to_broker.get(item.institution)
        pregao = item.pregao_date
        if broker_name is None or pregao is None:
            continue
        if (broker and broker_name != broker) or (start and pregao < start) or (end and pregao > end):
            continue
        if broker_name in ledger:
            instrument = current_symbol(item.instrument or "", former)
            cb_trades.append(
                Trade(broker_name, pregao, item.currency, instrument, item.quantity, item.amount)
            )
        else:
            cb.append(Movement(broker_name, pregao, item.currency, item.amount))
    warnings = link_warnings(
        {movement.broker for movement in crv} | {trade.broker for trade in crv_trades},
        broker_institutions(),
        settlements.institutions,
        category_found=settlements.category_found,
    )
    return Reconciliation(
        rows=_in_order(
            compare(cb, crv, tolerance=tolerance, coverage=coverage)
            + compare_trades(cb_trades, crv_trades, tolerance=tolerance, coverage=coverage)
        ),
        tolerance=tolerance,
        cb_status="ok",
        without_pregao=[
            item for item in settlements.without_pregao
            if currency == ALL or item.currency == currency
        ],
        futures_settlements=sum(
            1 for item in settlements.futures if currency == ALL or item.currency == currency
        ),
        executions_out_of_scope=out_of_scope,
        holdings=holdings_rows(
            owner_id,
            {
                key: statements
                for key, statements in settlements.holdings.items()
                if currency == ALL or key[1] == currency
            },
            institution_to_broker,
            broker=broker,
            start=start,
            end=end,
        ),
        warnings=warnings,
    )


def link_warnings(
    brokers: Iterable[str],
    institutions_by_broker: Mapping[str, tuple[str, ...]],
    cb_institutions: frozenset[str],
    *,
    category_found: bool,
) -> list[str]:
    """O que deixou de casar entre os nomes do CRV e os do CB. Função pura.

    ``brokers`` são as corretoras com negócio no CRV no recorte: só elas contam,
    para a corretora sem movimento não virar aviso.
    """
    warnings: list[str] = []
    if not category_found:
        warnings.append(
            f"O CB não tem a categoria “{SETTLEMENT_CATEGORY}”: só entraram as liquidações que o "
            "extrato da corretora marcou com a data do pregão. Se ela foi renomeada no CB, "
            "os lançamentos digitados à mão ficaram de fora."
        )
    for broker in sorted(set(brokers)):
        expected = institutions_by_broker.get(broker, ())
        if any(institution in cb_institutions for institution in expected):
            continue
        detail = (
            f"esperava a instituição {', '.join(expected)}" if expected
            else "ela não está em CB_CORRESPONDENCIA_CONTAS"
        )
        warnings.append(
            f"Nenhuma conta do CB corresponde à corretora {broker} ({detail}): os pregões dela "
            "aparecem como “só no CRV”. Ajuste CB_CORRESPONDENCIA_CONTAS ou o nome da instituição no CB."
        )
    return warnings


def _in_order(rows: list[ReconciliationRow]) -> list[ReconciliationRow]:
    return sorted(rows, key=lambda row: (row.pregao, row.broker, row.currency, row.instrument))
