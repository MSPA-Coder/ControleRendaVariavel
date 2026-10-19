"""Proventos do CB no banco do CRV: ensaio, gravacao, substituicao e desfazer.

Dois comandos usam este modulo (``importar-proventos-cb`` e
``substituir-proventos-planilha-2026``) e a tela de Proventos usa o corte e a
regra de escopo. O dono do dado e sempre explicito (``owner_id``): consulta e
gravacao filtram por ele.

Os ``plan_*`` so leem. Os ``apply_*`` nao fazem commit: a transacao e de quem
chama. Esta rotina so toca linhas ``origin = 'cb'`` (importar) e marca/desmarca
``superseded_at`` em linhas ``origin = 'planilha'`` (substituir); nunca apaga.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from flask import current_app
from sqlalchemy import select

from app import db
from app.accounts.entitlements import grant_ticker_entitlement
from app.dividends.cb_income import CbProvento, IncomeResult, build_proventos
from app.dividends.substituicao import CbLine, MatchResult, SheetLine, match_lines
from app.executions.conferencia import broker_institutions
from app.models import (
    DIVIDEND_ORIGIN_CB,
    DIVIDEND_ORIGIN_SHEET,
    Broker,
    Dividend,
    IncomeKind,
    Ticker,
)
from app.patrimonio.cb_client import CbIncome

DEFAULT_CUTOFF = date(2026, 1, 1)
DEFAULT_CB_BROKERS = ("Genial", "XP")
#: Moeda das contas cujo provento o CB assume.
CB_CURRENCY = "BRL"

CB_ORIGIN_MESSAGE = (
    "Proventos em reais da {broker} a partir de {cutoff:%d/%m/%Y} vêm do Controle Bancário "
    "(dono do dinheiro) e não podem ser lançados nem importados por planilha aqui."
)


# --- configuracao ----------------------------------------------------------


def cutoff_date(config: Mapping[str, object] | None = None) -> date:
    """Data de corte (``PROVENTOS_CB_CORTE``, AAAA-MM-DD); padrao 01/01/2026."""
    raw = (config if config is not None else current_app.config).get("PROVENTOS_CB_CORTE")
    if not raw:
        return DEFAULT_CUTOFF
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise ValueError("PROVENTOS_CB_CORTE deve estar no formato AAAA-MM-DD.") from exc


def cb_brokers(config: Mapping[str, object] | None = None) -> tuple[str, ...]:
    """Corretoras (nomes no CRV) cujo provento em reais vem do CB."""
    raw = (config if config is not None else current_app.config).get("PROVENTOS_CB_CORRETORAS")
    names = tuple(part.strip() for part in str(raw or "").split(",") if part.strip())
    return names or DEFAULT_CB_BROKERS


def broker_by_institution(config: Mapping[str, object] | None = None) -> dict[str, str]:
    """Instituicao do CB -> corretora do CRV, so das corretoras que o CB assume."""
    allowed = {name.casefold() for name in cb_brokers(config)}
    result: dict[str, str] = {}
    for broker_name, institutions in broker_institutions(config).items():
        if broker_name.casefold() in allowed:
            for institution in institutions:
                result[institution] = broker_name
    return result


def comes_from_cb(*, currency: str, broker_name: str, payment_date: date) -> bool:
    """Este provento (ticker em ``currency``, corretora, data) e do CB, nao da planilha?"""
    return (
        currency == CB_CURRENCY
        and broker_name.casefold() in {name.casefold() for name in cb_brokers()}
        and payment_date >= cutoff_date()
    )


def refusal_message(broker_name: str) -> str:
    return CB_ORIGIN_MESSAGE.format(broker=broker_name, cutoff=cutoff_date())


# --- importar ---------------------------------------------------------------


@dataclass(slots=True)
class PreparedRow:
    provento: CbProvento
    ticker_id: int
    broker_id: int
    existing: Dividend | None = None


@dataclass(slots=True)
class ImportPlan:
    income: IncomeResult
    cutoff: date
    not_realized: int = 0
    create: list[PreparedRow] = field(default_factory=list)
    update: list[PreparedRow] = field(default_factory=list)
    unchanged: int = 0
    #: Linhas ``cb`` do CRV cujo credito nao esta mais entre os do CB.
    absent_from_cb: list[Dividend] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def can_write(self) -> bool:
        return bool(self.create or self.update)


def _same_values(row: Dividend, prepared: PreparedRow) -> bool:
    provento = prepared.provento
    return (
        row.ticker_id == prepared.ticker_id
        and row.broker_id == prepared.broker_id
        and row.kind == IncomeKind(provento.kind)
        and row.payment_date == provento.payment_date
        and row.amount == provento.net
        and row.gross_amount == provento.gross
        and row.withheld_amount == provento.withheld
    )


def plan_cb_import(owner_id: int, income: CbIncome, *, cutoff: date | None = None) -> ImportPlan:
    """Ensaio: o que a importacao criaria, atualizaria, deixaria e mandaria a revisao."""
    cutoff = cutoff or cutoff_date()
    tickers = {
        ticker.symbol.upper(): ticker
        for ticker in db.session.scalars(select(Ticker).where(Ticker.is_benchmark.is_(False)))
    }
    brokers = {broker.name.casefold(): broker for broker in db.session.scalars(select(Broker))}
    result = build_proventos(
        income.entries,
        cutoff=cutoff,
        broker_by_institution=broker_by_institution(),
        known_tickers=set(tickers),
    )
    plan = ImportPlan(income=result, cutoff=cutoff, not_realized=income.not_realized)

    existing = {
        row.origin_id: row
        for row in db.session.scalars(
            select(Dividend).where(Dividend.owner_id == owner_id, Dividend.origin == DIVIDEND_ORIGIN_CB)
        )
    }
    seen: set[str] = set()
    missing_brokers: set[str] = set()
    for provento in result.proventos:
        broker = brokers.get(provento.broker.casefold())
        if broker is None:
            missing_brokers.add(provento.broker)
            continue
        ticker = tickers[provento.ticker]
        if ticker.currency != CB_CURRENCY:
            plan.problems.append(
                f"{provento.ticker} está cadastrado em {ticker.currency}, mas o crédito {provento.origin_id} do CB é em reais."
            )
            continue
        prepared = PreparedRow(provento, ticker.id, broker.id, existing.get(provento.origin_id))
        seen.add(provento.origin_id)
        if prepared.existing is None:
            plan.create.append(prepared)
        elif _same_values(prepared.existing, prepared):
            plan.unchanged += 1
        else:
            plan.update.append(prepared)
    for name in sorted(missing_brokers):
        plan.problems.append(f"A corretora '{name}' não está cadastrada no CRV.")
    plan.absent_from_cb = sorted(
        (row for origin_id, row in existing.items() if origin_id not in seen and row.payment_date >= cutoff),
        key=lambda row: (row.payment_date, row.id),
    )
    return plan


def apply_cb_import(owner_id: int, plan: ImportPlan) -> tuple[int, int]:
    """Grava o plano (sem commit). Devolve (criadas, atualizadas)."""
    for prepared in plan.create:
        provento = prepared.provento
        db.session.add(
            Dividend(
                owner_id=owner_id,
                origin=DIVIDEND_ORIGIN_CB,
                origin_id=provento.origin_id,
                ticker_id=prepared.ticker_id,
                broker_id=prepared.broker_id,
                kind=IncomeKind(provento.kind),
                payment_date=provento.payment_date,
                amount=provento.net,
                gross_amount=provento.gross,
                withheld_amount=provento.withheld,
            )
        )
        grant_ticker_entitlement(user_id=owner_id, ticker_id=prepared.ticker_id, held_on=provento.payment_date)
    for prepared in plan.update:
        row = prepared.existing
        assert row is not None and row.owner_id == owner_id and row.origin == DIVIDEND_ORIGIN_CB
        provento = prepared.provento
        row.ticker_id = prepared.ticker_id
        row.broker_id = prepared.broker_id
        row.kind = IncomeKind(provento.kind)
        row.payment_date = provento.payment_date
        row.amount = provento.net
        row.gross_amount = provento.gross
        row.withheld_amount = provento.withheld
        grant_ticker_entitlement(user_id=owner_id, ticker_id=prepared.ticker_id, held_on=provento.payment_date)
    db.session.flush()
    return len(plan.create), len(plan.update)


# --- substituir a planilha --------------------------------------------------


@dataclass(slots=True)
class SubstitutionPlan:
    match: MatchResult
    cutoff: date
    #: Linhas de planilha elegiveis (reais, corretora do CB, a partir do corte).
    eligible: int
    rows_by_id: dict[int, Dividend]
    already_superseded: int = 0
    #: Creditos do CB que ja tem a linha de planilha substituida (reexecucao).
    covered_cb: int = 0

    @property
    def to_supersede(self) -> list[Dividend]:
        return [self.rows_by_id[line_id] for line_id in self.match.matched_sheet_ids]

    @property
    def divergences(self) -> list[Dividend]:
        return [self.rows_by_id[line.id] for line in self.match.unmatched_sheet]


def plan_substitution(owner_id: int, *, cutoff: date | None = None) -> SubstitutionPlan:
    """Ensaio: pareia as linhas de planilha do corte em diante com as do CB ja importadas."""
    cutoff = cutoff or cutoff_date()
    allowed = {name.casefold() for name in cb_brokers()}
    statement = (
        select(Dividend)
        .join(Dividend.ticker_ref)
        .join(Dividend.broker_ref)
        .where(
            Dividend.owner_id == owner_id,
            Dividend.payment_date >= cutoff,
            Ticker.currency == CB_CURRENCY,
        )
        .order_by(Dividend.payment_date, Dividend.id)
    )
    sheet_rows: dict[int, Dividend] = {}
    cb_lines: list[CbLine] = []
    superseded_lines: list[SheetLine] = []
    already = 0
    for row in db.session.scalars(statement):
        if row.broker.casefold() not in allowed:
            continue
        if row.origin == DIVIDEND_ORIGIN_CB:
            assert row.gross_amount is not None
            cb_lines.append(
                CbLine(row.id, row.broker, row.ticker, row.kind.value, row.payment_date, row.gross_amount, row.amount)
            )
        elif row.origin == DIVIDEND_ORIGIN_SHEET:
            if row.superseded_at is not None:
                already += 1
                superseded_lines.append(
                    SheetLine(row.id, row.broker, row.ticker, row.kind.value, row.payment_date, row.amount)
                )
            else:
                sheet_rows[row.id] = row
    lines = [
        SheetLine(row.id, row.broker, row.ticker, row.kind.value, row.payment_date, row.amount)
        for row in sheet_rows.values()
    ]
    match = match_lines(lines, cb_lines)
    # Na reexecucao as linhas ja foram marcadas e nao entram mais no pareamento:
    # um credito que sobra mas tem par entre elas esta coberto, nao "sem linha".
    covered_ids = {pair.cb.id for pair in match_lines(superseded_lines, match.unmatched_cb).pairs}
    match.unmatched_cb = [credit for credit in match.unmatched_cb if credit.id not in covered_ids]
    return SubstitutionPlan(
        match=match,
        cutoff=cutoff,
        eligible=len(sheet_rows),
        rows_by_id=sheet_rows,
        already_superseded=already,
        covered_cb=len(covered_ids),
    )


def apply_substitution(owner_id: int, plan: SubstitutionPlan, *, now: datetime | None = None) -> int:
    """Marca como substituidas as linhas pareadas (sem commit). Devolve quantas."""
    moment = now or datetime.now(UTC)
    rows = plan.to_supersede
    for row in rows:
        assert row.owner_id == owner_id and row.origin == DIVIDEND_ORIGIN_SHEET
        row.superseded_at = moment
    db.session.flush()
    return len(rows)


def superseded_rows(owner_id: int, *, cutoff: date | None = None) -> list[Dividend]:
    cutoff = cutoff or cutoff_date()
    return list(
        db.session.scalars(
            select(Dividend)
            .where(
                Dividend.owner_id == owner_id,
                Dividend.origin == DIVIDEND_ORIGIN_SHEET,
                Dividend.superseded_at.is_not(None),
                Dividend.payment_date >= cutoff,
            )
            .order_by(Dividend.payment_date, Dividend.id)
        )
    )


def undo_substitution(owner_id: int, *, cutoff: date | None = None) -> int:
    """Devolve as linhas substituidas a planilha ativa (sem commit). Devolve quantas."""
    rows = superseded_rows(owner_id, cutoff=cutoff)
    for row in rows:
        row.superseded_at = None
    db.session.flush()
    return len(rows)
