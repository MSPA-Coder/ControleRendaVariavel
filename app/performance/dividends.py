from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
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
    cost_basis: Decimal | None
    """Custo de aquisição atual do ativo (soma de quantidade × custo médio
    das posições REAIS ainda abertas nesse ticker, ver
    ``app.routes.helpers.open_real_cost_basis_by_ticker``). ``None`` quando o ativo não tem
    posição aberta hoje — o custo de aquisição de uma posição já encerrada
    não fica registrado isoladamente por ativo em nenhum lugar do modelo,
    então ``yield_on_cost`` também fica ``None`` nesse caso."""
    yield_on_cost: Decimal | None
    """Proventos recebidos ÷ custo de aquisição (Relatório de
    Proventos). Ver ``cost_basis`` para quando fica indisponível."""
    entries: list[Dividend]
    """Os lançamentos individuais deste ativo, na mesma ordem de
    ``dividends`` (mais recente primeiro, garantida pela consulta em
    ``routes.dividends``) — o drill-down do card "Por ativo" na tela de
    Proventos, aberto pelo `+` como o extrato de uma posição em Carteira."""


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
    cost_basis_by_ticker: Mapping[int, Decimal],
) -> DividendReport:
    """Agrega proventos por ativo e por ano, cada um com seu drill-down.

    ``cost_basis_by_ticker`` é fornecido pelo chamador (ver
    ``routes.dividends._cost_basis_by_ticker``) em vez de calculado aqui,
    para manter esta função pura e testável com dados simples — sem
    depender de sessão de banco.

    Espera ``dividends`` já ordenado do mais recente ao mais antigo (mesma
    consulta da lista bruta em ``routes.dividends``): é essa ordem que
    ``TickerDividendTotal.entries`` preserva, sem reordenar.
    """
    ticker_totals: dict[tuple[str, str], Decimal] = {}
    ticker_ids: dict[tuple[str, str], int] = {}
    ticker_kind_totals: dict[tuple[str, str], dict[str, Decimal]] = {}
    ticker_cost_basis: dict[tuple[str, str], Decimal | None] = {}
    ticker_entries: dict[tuple[str, str], list[Dividend]] = {}
    month_totals: dict[tuple[date, str], Decimal] = {}
    for dividend in dividends:
        ticker_key = (dividend.ticker, dividend.currency)
        ticker_totals[ticker_key] = ticker_totals.get(ticker_key, Decimal("0")) + dividend.amount
        ticker_ids[ticker_key] = dividend.ticker_id
        by_kind = ticker_kind_totals.setdefault(ticker_key, {})
        by_kind[dividend.kind] = by_kind.get(dividend.kind, Decimal("0")) + dividend.amount
        ticker_cost_basis[ticker_key] = cost_basis_by_ticker.get(dividend.ticker_id)
        ticker_entries.setdefault(ticker_key, []).append(dividend)
        month_key = (dividend.payment_date.replace(day=1), dividend.currency)
        month_totals[month_key] = month_totals.get(month_key, Decimal("0")) + dividend.amount

    by_ticker = []
    for (ticker, currency), total in sorted(ticker_totals.items()):
        cost_basis = ticker_cost_basis[(ticker, currency)]
        yield_on_cost = safe_div(total, cost_basis) if cost_basis is not None else None
        by_ticker.append(
            TickerDividendTotal(
                ticker=ticker,
                ticker_id=ticker_ids[(ticker, currency)],
                currency=currency,
                total_amount=total,
                amount_by_kind=ticker_kind_totals[(ticker, currency)],
                cost_basis=cost_basis,
                yield_on_cost=yield_on_cost,
                entries=ticker_entries[(ticker, currency)],
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
