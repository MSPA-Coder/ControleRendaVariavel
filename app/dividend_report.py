from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain import safe_div
from app.models import Dividend


@dataclass(frozen=True, slots=True)
class TickerDividendTotal:
    ticker: str
    currency: str
    total_amount: Decimal
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


@dataclass(frozen=True, slots=True)
class MonthDividendTotal:
    month: date
    """Primeiro dia do mês do pagamento, usado apenas para ordenação e
    formatação (ex.: ``month.strftime('%m/%Y')``)."""
    currency: str
    total_amount: Decimal


@dataclass(frozen=True, slots=True)
class DividendReport:
    by_ticker: list[TickerDividendTotal]
    by_month: list[MonthDividendTotal]


def build_dividend_report(
    dividends: list[Dividend],
    cost_basis_by_ticker: Mapping[int, Decimal],
) -> DividendReport:
    """Agrega proventos por ativo e por mês.

    ``cost_basis_by_ticker`` é fornecido pelo chamador (ver
    ``routes.dividends._cost_basis_by_ticker``) em vez de calculado aqui,
    para manter esta função pura e testável com dados simples — sem
    depender de sessão de banco.
    """
    ticker_totals: dict[tuple[str, str], Decimal] = {}
    ticker_cost_basis: dict[tuple[str, str], Decimal | None] = {}
    month_totals: dict[tuple[date, str], Decimal] = {}
    for dividend in dividends:
        ticker_key = (dividend.ticker, dividend.currency)
        ticker_totals[ticker_key] = ticker_totals.get(ticker_key, Decimal("0")) + dividend.amount
        ticker_cost_basis[ticker_key] = cost_basis_by_ticker.get(dividend.ticker_id)
        month_key = (dividend.payment_date.replace(day=1), dividend.currency)
        month_totals[month_key] = month_totals.get(month_key, Decimal("0")) + dividend.amount

    by_ticker = []
    for (ticker, currency), total in sorted(ticker_totals.items()):
        cost_basis = ticker_cost_basis[(ticker, currency)]
        yield_on_cost = safe_div(total, cost_basis) if cost_basis is not None else None
        by_ticker.append(
            TickerDividendTotal(
                ticker=ticker,
                currency=currency,
                total_amount=total,
                cost_basis=cost_basis,
                yield_on_cost=yield_on_cost,
            )
        )
    by_month = [
        MonthDividendTotal(month=month, currency=currency, total_amount=total)
        for (month, currency), total in sorted(month_totals.items())
    ]
    return DividendReport(by_ticker=by_ticker, by_month=by_month)
