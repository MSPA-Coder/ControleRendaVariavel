"""Relatório de Performance mensal (item 6 do plano revisado: o mais
dependente de dados acumulados em ``quote_history``).

Reaproveita ``app.risk.portfolio_value_series`` e, portanto, a mesma
aproximação documentada lá: assume as quantidades ATUAIS das posições
REAIS constantes ao longo de todo o histórico simulado. Simplificação
combinada com o usuário: sem tratamento rigoroso de aportes/retiradas
(sem Dietz/IRR) — só o valor da carteira ao longo do tempo, reduzido a um
ponto por mês (o último valor observado dentro de cada mês).
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.risk import portfolio_value_series


@dataclass(frozen=True, slots=True)
class MonthlyPerformancePoint:
    month: date
    """Primeiro dia do mês, usado apenas para ordenação/formatação."""
    ending_value: Decimal
    """Valor da carteira no último ponto disponível dentro do mês."""
    return_pct: Decimal | None
    """Variação percentual em relação ao ``ending_value`` do mês anterior
    na série. ``None`` no primeiro mês (não há mês anterior para
    comparar) ou se o valor do mês anterior for zero."""


@dataclass(frozen=True, slots=True)
class MonthlyPerformanceReport:
    currency: str
    points: list[MonthlyPerformancePoint]


PERFORMANCE_PERIODS = frozenset({"week", "month", "quarter", "semester", "year", "all"})
"""Períodos aceitos pela rota de performance."""


def normalize_performance_period(value: str | None) -> str:
    """Normaliza o filtro de período na fronteira HTTP.

    ``all`` é o comportamento padrão para que o relatório continue mostrando
    todo o histórico disponível quando a URL não contém filtro.
    """
    return value if value in PERFORMANCE_PERIODS else "all"


def _subtract_calendar_months(value: date, months: int) -> date:
    """Volta meses de calendário sem usar ``float`` nem depender de pacotes extras."""
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def select_performance_period(
    values: Sequence[tuple[date, Decimal]], period: str
) -> list[tuple[date, Decimal]]:
    """Recorta uma série pelo período solicitado, no backend.

    A referência é a última cotação existente, e não ``date.today()``. Isso
    mantém relatórios históricos reproduzíveis e permite navegar por dados que
    ainda não tenham sido atualizados até o dia corrente.
    """
    period = normalize_performance_period(period)
    if not values or period == "all":
        return list(values)

    latest_date = values[-1][0]
    starts = {
        "week": latest_date - timedelta(days=6),
        "month": _subtract_calendar_months(latest_date, 1),
        "quarter": _subtract_calendar_months(latest_date, 3),
        "semester": _subtract_calendar_months(latest_date, 6),
        "year": _subtract_calendar_months(latest_date, 12),
    }
    start_date = starts[period]
    return [
        (observed_date, value) for observed_date, value in values if observed_date >= start_date
    ]


def _month_end_values(values: Sequence[tuple[date, Decimal]]) -> list[tuple[date, Decimal]]:
    """Reduz a série diária a um ponto por mês: o último valor observado
    dentro de cada mês. Assume ``values`` já ordenado por data (garantido
    por ``portfolio_value_series``)."""
    last_by_month: dict[date, Decimal] = {}
    for observed_date, value in values:
        last_by_month[observed_date.replace(day=1)] = value
    return sorted(last_by_month.items())


def build_monthly_performance(
    currency: str,
    quantities: Mapping[Any, Decimal],
    price_series: Mapping[Any, Sequence[tuple[date, Decimal]]],
    period: str = "all",
) -> MonthlyPerformanceReport:
    values = portfolio_value_series(quantities, price_series, require_all_tickers=False)
    month_ends = _month_end_values(select_performance_period(values, period))

    points: list[MonthlyPerformancePoint] = []
    previous_value: Decimal | None = None
    for month, ending_value in month_ends:
        return_pct = (
            (ending_value - previous_value) / previous_value
            if previous_value
            else None
        )
        points.append(
            MonthlyPerformancePoint(month=month, ending_value=ending_value, return_pct=return_pct)
        )
        previous_value = ending_value
    return MonthlyPerformanceReport(currency=currency, points=points)
