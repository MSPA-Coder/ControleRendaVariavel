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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
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
) -> MonthlyPerformanceReport:
    values = portfolio_value_series(quantities, price_series)
    month_ends = _month_end_values(values)

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
