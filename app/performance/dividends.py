from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.core.domain import safe_div
from app.models import Dividend


@dataclass(frozen=True, slots=True)
class TickerDividendTotal:
    ticker: str
    ticker_id: int
    currency: str
    total_amount: Decimal
    amount_by_kind: Mapping[str, Decimal]
    """Recebido por tipo de renda (``IncomeKind``: dividendo, JCP, aluguel).
    Só os tipos com valor aparecem; o template itera ``IncomeKind`` para
    exibir as três colunas sempre, com "-" onde não houver renda daquele
    tipo — mesmo padrão de ``MonthlyPerformancePoint.income_by_kind``."""
    entries: list[Dividend]
    """Os lançamentos individuais deste ativo, na mesma ordem de
    ``dividends`` (mais recente primeiro, garantida pela consulta em
    ``routes.dividends``) — o drill-down do card "Por ativo" na tela de
    Proventos, aberto pelo `+` como o extrato de uma posição em Carteira."""
    dividend_yield_12m: Decimal | None
    """Proventos por cota nos últimos 365 dias ÷ cotação atual."""
    yield_on_cost_current: Decimal | None
    """Todos os proventos recebidos ÷ custo das posições reais abertas."""


@dataclass(frozen=True, slots=True)
class MonthDividendTotal:
    month: date
    """Primeiro dia do mês do pagamento, usado apenas para ordenação e
    formatação (ex.: ``month.strftime('%m/%Y')``)."""
    currency: str
    total_amount: Decimal


@dataclass(frozen=True, slots=True)
class YearDividendTotal:
    year: int
    currency: str
    total_amount: Decimal
    months: list[MonthDividendTotal]
    """Meses daquele ano com provento, do mais recente ao mais antigo — o
    drill-down do card "Por ano"."""


@dataclass(frozen=True, slots=True)
class DividendReport:
    by_ticker: list[TickerDividendTotal]
    by_year: list[YearDividendTotal]


def build_dividend_report(
    dividends: list[Dividend],
    *,
    cost_basis_by_ticker: Mapping[int, Decimal],
    quotes_by_ticker: Mapping[int, Decimal],
    reference_date: date | None = None,
) -> DividendReport:
    """Agrega proventos por ativo e por ano, cada um com seu drill-down.

    Espera ``dividends`` já ordenado do mais recente ao mais antigo (mesma
    consulta da lista bruta em ``routes.dividends``): é essa ordem que
    ``TickerDividendTotal.entries`` preserva, sem reordenar.
    """
    reference_date = reference_date or date.today()
    trailing_start = reference_date - timedelta(days=365)
    ticker_totals: dict[tuple[str, str], Decimal] = {}
    ticker_ids: dict[tuple[str, str], int] = {}
    ticker_kind_totals: dict[tuple[str, str], dict[str, Decimal]] = {}
    ticker_entries: dict[tuple[str, str], list[Dividend]] = {}
    trailing_per_share: dict[tuple[str, str], Decimal] = {}
    month_totals: dict[tuple[date, str], Decimal] = {}
    for dividend in dividends:
        ticker_key = (dividend.ticker, dividend.currency)
        ticker_totals[ticker_key] = ticker_totals.get(ticker_key, Decimal("0")) + dividend.amount
        ticker_ids[ticker_key] = dividend.ticker_id
        by_kind = ticker_kind_totals.setdefault(ticker_key, {})
        by_kind[dividend.kind] = by_kind.get(dividend.kind, Decimal("0")) + dividend.amount
        ticker_entries.setdefault(ticker_key, []).append(dividend)
        if dividend.payment_date >= trailing_start:
            amount_per_share = dividend.amount_per_share
            if amount_per_share is None and dividend.quotas:
                amount_per_share = dividend.amount / dividend.quotas
            if amount_per_share is not None:
                trailing_per_share[ticker_key] = (
                    trailing_per_share.get(ticker_key, Decimal("0")) + amount_per_share
                )
        month_key = (dividend.payment_date.replace(day=1), dividend.currency)
        month_totals[month_key] = month_totals.get(month_key, Decimal("0")) + dividend.amount

    by_ticker = []
    for (ticker, currency), total in sorted(ticker_totals.items()):
        by_ticker.append(
            TickerDividendTotal(
                ticker=ticker,
                ticker_id=ticker_ids[(ticker, currency)],
                currency=currency,
                total_amount=total,
                amount_by_kind=ticker_kind_totals[(ticker, currency)],
                entries=ticker_entries[(ticker, currency)],
                dividend_yield_12m=safe_div(
                    trailing_per_share.get((ticker, currency), Decimal("0")),
                    quotes_by_ticker.get(ticker_ids[(ticker, currency)], Decimal("0")),
                ),
                yield_on_cost_current=safe_div(
                    total,
                    cost_basis_by_ticker.get(ticker_ids[(ticker, currency)], Decimal("0")),
                ),
            )
        )

    # Meses do mais recente ao mais antigo, e já agrupados dentro do ano a
    # que pertencem: iterar em ordem decrescente e ir anexando dispensa
    # reordenar `months` depois de formar cada `YearDividendTotal`.
    year_totals: dict[tuple[int, str], Decimal] = {}
    year_months: dict[tuple[int, str], list[MonthDividendTotal]] = {}
    for (month, currency), total in sorted(month_totals.items(), reverse=True):
        year_key = (month.year, currency)
        year_totals[year_key] = year_totals.get(year_key, Decimal("0")) + total
        year_months.setdefault(year_key, []).append(
            MonthDividendTotal(month=month, currency=currency, total_amount=total)
        )
    by_year = [
        YearDividendTotal(
            year=year,
            currency=currency,
            total_amount=total,
            months=year_months[(year, currency)],
        )
        for (year, currency), total in sorted(year_totals.items(), reverse=True)
    ]

    return DividendReport(by_ticker=by_ticker, by_year=by_year)
