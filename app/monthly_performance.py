"""Relatório de Performance mensal (o mais
dependente de dados acumulados em ``quote_history``).

Reaproveita ``app.risk.portfolio_value_series`` e, portanto, a mesma
aproximação documentada lá: assume as quantidades ATUAIS das posições
REAIS constantes ao longo de todo o histórico simulado. Simplificação
combinada com o usuário: sem tratamento rigoroso de aportes/retiradas
(sem Dietz/IRR) — só o valor da carteira ao longo do tempo, reduzido a um
ponto por mês (o último valor observado dentro de cada mês).

O comparador de índice (``build_benchmark_shadow_series``) usa a mesma
aproximação de quantidade/custo atual constante, mas trata a DATA de cada
ativo individualmente: cada posição só entra na curva hipotética a partir
da sua própria abertura, e não da abertura da carteira. Isso é o que
mantém a comparação com sentido mesmo com aportes em datas diferentes —
ver o docstring da função.
"""

from __future__ import annotations

from bisect import bisect_left
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


def align_benchmark_to_points(
    points: Sequence[MonthlyPerformancePoint],
    benchmark_series: Sequence[tuple[date, Decimal]],
) -> list[Decimal | None]:
    """Reduz a série diária de um ticker de referência a um valor por mês (o
    último disponível dentro do mês, mesmo critério de ``_month_end_values``),
    alinhado aos meses de ``points`` para permitir comparar a evolução da
    carteira com a de um índice no gráfico de performance mensal.

    Meses sem cotação do índice viram ``None`` (buraco preservado, nunca
    interpolado) em vez de serem omitidos, para que o índice de cada ponto em
    ``points`` continue correspondendo pela posição na lista resultante.

    Genérica o bastante para alinhar tanto uma série de preço bruto quanto
    o valor de ``build_benchmark_shadow_series`` — ver ``routes.performance``.
    """
    month_ends = dict(_month_end_values(benchmark_series))
    return [month_ends.get(point.month) for point in points]


def build_benchmark_shadow_series(
    contributions: Sequence[tuple[date, Decimal]],
    benchmark_series: Sequence[tuple[date, Decimal]],
) -> list[tuple[date, Decimal]]:
    """Curva de "quanto valeria hoje se, em vez de cada ativo comprado, o
    mesmo valor tivesse sido aplicado no benchmark na mesma data" — a
    comparação que de fato responde "eu me saí melhor comprando essas
    ações ou teria sido melhor no índice?" (combinado com o usuário: a
    comparação anterior, carteira em R$ absoluto vs. preço do índice
    rebaseado a %, não fazia sentido porque um novo aporte faz o R$ da
    carteira saltar sem relação com desempenho).

    Cada item de ``contributions`` é ``(data_de_abertura, valor_investido)``
    de UM ticker — ``quantidade × custo médio atual`` na primeira posição
    daquele ticker (``opened_on_by_ticker``/``invested_amount_by_ticker`` em
    ``routes.performance``). Mesma aproximação de ponto único já usada em
    ``routes.helpers.ticker_position_start_date`` para o gráfico de
    Cotações: sem um livro-razão de lotes por compra individual, é o melhor
    proxy disponível — ancora todo o custo médio atual na abertura da
    posição, em vez de tentar (sem dado para isso) repartir por cada
    compra que formou aquela média.

    O ponto crucial para carteiras com múltiplos ativos comprados em datas
    diferentes: cada contribuição só passa a valer a partir da SUA PRÓPRIA
    data (soma zero antes disso), replicando o mesmo efeito de "aportes
    sucessivos" que a carteira real tem — ``portfolio_value_series`` já
    funciona assim (cada ticker só entra na soma a partir da primeira
    cotação disponível). É isso que resolve a distorção de comparar uma
    carteira que recebe aportes com um índice de base fixa: os dois lados
    "recebem" o mesmo aporte, na mesma data.

    Retorna a série diária somada, nas datas em que o benchmark tem
    cotação (mesma malha de datas do benchmark, com forward-fill implícito
    do valor investido por contribuição já ativa).
    """
    ordered_benchmark = sorted(benchmark_series)
    if not ordered_benchmark:
        return []
    bench_dates = [observed_date for observed_date, _ in ordered_benchmark]
    bench_prices = [price for _, price in ordered_benchmark]

    anchors: list[tuple[date, Decimal, Decimal]] = []
    for entry_date, invested_amount in contributions:
        if invested_amount <= 0:
            continue
        index = bisect_left(bench_dates, entry_date)
        anchor_price = bench_prices[index] if index < len(bench_dates) else bench_prices[-1]
        if anchor_price <= 0:
            continue
        anchors.append((entry_date, invested_amount, anchor_price))
    if not anchors:
        return []

    result: list[tuple[date, Decimal]] = []
    for current_date, current_price in ordered_benchmark:
        total = sum(
            (
                invested_amount * current_price / anchor_price
                for entry_date, invested_amount, anchor_price in anchors
                if current_date >= entry_date
            ),
            Decimal("0"),
        )
        result.append((current_date, total))
    return result
