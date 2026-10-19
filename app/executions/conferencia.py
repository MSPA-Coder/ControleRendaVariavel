"""Conferência por pregão: liquidação do CB contra as execuções do myProfit.

Só leitura. Nenhum lado grava no outro: a diferença é mostrada, e ela é o
**custo implícito** do pregão — o que a corretora e a liquidação levaram além
das taxas da B3 que o myProfit já contabiliza (o CRV não guarda custos).

A comparação é por corretora, data do pregão e moeda:

* CB: soma assinada dos lançamentos "Operações em Bolsa" da(s) conta(s) da
  corretora (venda positiva, compra negativa);
* CRV: soma de ``total_with_fees`` das execuções **em escopo** (ações, opções e
  ETF da B3), que já vem com o sinal do caixa.

``compare`` é pura; ``build_reconciliation`` é a ligação com o banco e o CB.
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
from app.models import Broker, Execution
from app.patrimonio.cb_client import (
    CbConfig,
    CbSettlement,
    CbUnavailableError,
    Fetcher,
    parse_config,
    read_settlements,
)

DEFAULT_TOLERANCE = Decimal("5.00")

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

    @property
    def state_label(self) -> str:
        return STATE_LABELS[self.state]


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
            rows=compare([], crv, tolerance=tolerance, cb_available=False),
            tolerance=tolerance,
            cb_status=status,
            cb_message=message,
            executions_out_of_scope=out_of_scope,
        )
    try:
        settlements = read_settlements(config, fetcher=fetcher)
    except CbUnavailableError as exc:
        return Reconciliation(
            rows=compare([], crv, tolerance=tolerance, cb_available=False),
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
    for (institution, currency), covered in settlements.coverage.items():
        broker_name = institution_to_broker.get(institution)
        if broker_name is not None:
            key = (broker_name, currency)
            coverage[key] = max(covered, coverage.get(key, covered))
    cb: list[Movement] = []
    for item in settlements.items:
        broker_name = institution_to_broker.get(item.institution)
        pregao = item.pregao_date
        if broker_name is None or pregao is None:
            continue
        if (broker and broker_name != broker) or (start and pregao < start) or (end and pregao > end):
            continue
        cb.append(Movement(broker_name, pregao, item.currency, item.amount))
    return Reconciliation(
        rows=compare(cb, crv, tolerance=tolerance, coverage=coverage),
        tolerance=tolerance,
        cb_status="ok",
        without_pregao=settlements.without_pregao,
        futures_settlements=len(settlements.futures),
        executions_out_of_scope=out_of_scope,
    )
