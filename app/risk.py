"""KPIs de risco (Fase D do plano revisado).

Segue a mesma convenção de ``app/greeks.py``: os cálculos estatísticos
contínuos (desvio padrão, percentil, covariância) usam ``float``
internamente — são matemática de modelo, não contabilidade — e só viram
``Decimal`` na fronteira de saída (``_to_decimal``), com a precisão
suficiente para não perder informação relevante. ``max_drawdown``, por ser
um simples rastreamento determinístico de mínimo/máximo sobre preços
exatos (sem nenhuma suposição distribucional), fica em ``Decimal`` do
início ao fim, como o resto da contabilidade do app.

Os números aqui só ficam representativos depois que ``quote_history``
acumular um histórico razoável por ticker — ver ``MIN_OBSERVATIONS_FOR_CONFIDENCE``,
usado pelas rotas para decidir quando exibir um aviso de "poucos dias
acumulados".
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

TRADING_DAYS_PER_YEAR = 252
"""Convenção usual para ativos negociados em bolsa (B3/NYSE/NASDAQ)."""

MIN_OBSERVATIONS_FOR_CONFIDENCE = 20
"""~1 mês de pregões. Abaixo disso os KPIs estatísticos (volatilidade,
Sharpe, Sortino, VaR, Beta) ainda são calculados, mas as rotas devem
sinalizar ao usuário que os números têm baixa significância estatística."""


def _to_decimal(value: float, places: str = "0.000001") -> Decimal | None:
    """Converte um resultado estatístico (float) para Decimal, com a
    precisão de saída dada por ``places``.

    Retorna ``None`` (em vez de propagar a exceção) quando o valor não é
    representável — não-finito (``nan``/``inf``) ou grande demais para a
    precisão padrão do Decimal. Isso acontece de verdade: o retorno
    anualizado extrapola janelas curtas para 365 dias (ver
    ``annualized_return_from_prices``), e uma variação de -20% em 1 dia
    projetada para um ano vira uma fração astronômica sem nenhum
    significado prático — melhor reportar "indisponível" do que um número
    inutilizável (ou derrubar a página).
    """
    if not math.isfinite(value):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal(places))
    except InvalidOperation:
        return None


def daily_returns_by_date(series: Sequence[tuple[date, Decimal]]) -> dict[date, float]:
    """Retornos diários simples (P_t / P_(t-1) - 1), indexados pela data
    do retorno (não pela data anterior). Duas séries com datas em comum
    ficam automaticamente alinhadas por essas chaves — é assim que
    ``beta`` casa os retornos do ativo com os do benchmark sem exigir que
    as duas séries tenham exatamente as mesmas datas em todos os pontos.
    """
    ordered = sorted(series)
    returns: dict[date, float] = {}
    previous_price: Decimal | None = None
    for observed_date, price in ordered:
        if previous_price is not None and previous_price > 0:
            returns[observed_date] = float((price - previous_price) / previous_price)
        previous_price = price
    return returns


def annualized_volatility(returns: Sequence[float]) -> float | None:
    """Desvio padrão amostral dos retornos diários, anualizado por
    √252. ``None`` com menos de 2 observações (desvio padrão indefinido)."""
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)


def downside_deviation(
    returns: Sequence[float], minimum_acceptable_return: float = 0.0
) -> float | None:
    """Desvio padrão anualizado considerando apenas os retornos abaixo de
    ``minimum_acceptable_return`` (0 por padrão), usado no Sortino."""
    if len(returns) < 2:
        return None
    downside_squared = [min(r - minimum_acceptable_return, 0.0) ** 2 for r in returns]
    mean_downside_squared = sum(downside_squared) / len(downside_squared)
    return math.sqrt(mean_downside_squared) * math.sqrt(TRADING_DAYS_PER_YEAR)


def historical_var(returns: Sequence[float], confidence: float = 0.95) -> float | None:
    """VaR histórico: percentil (1 - confidence) dos retornos diários (o
    5º percentil para 95% de confiança). Reportado com o sinal original do
    retorno — tipicamente negativo — sem conversão para uma magnitude de
    perda positiva; interpretar como "o pior retorno diário esperado em
    95% dos dias, historicamente"."""
    if len(returns) < 2:
        return None
    ordered = sorted(returns)
    n = len(ordered)
    position = (1 - confidence) * (n - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def annualized_return_from_prices(series: Sequence[tuple[date, Decimal]]) -> float | None:
    """CAGR entre a primeira e a última cotação disponível na série."""
    ordered = sorted(series)
    if len(ordered) < 2:
        return None
    first_date, first_price = ordered[0]
    last_date, last_price = ordered[-1]
    if first_price <= 0:
        return None
    elapsed_days = (last_date - first_date).days
    if elapsed_days <= 0:
        return None
    total_return = float(last_price / first_price) - 1.0
    sign = -1.0 if total_return < 0 else 1.0
    base = 1.0 + abs(total_return)
    return sign * (float(base ** (365.0 / elapsed_days)) - 1.0)


def sharpe_ratio(
    annualized_return_value: float | None,
    risk_free_rate_annual: float,
    volatility: float | None,
) -> float | None:
    if annualized_return_value is None or not volatility:
        return None
    return (annualized_return_value - risk_free_rate_annual) / volatility


def sortino_ratio(
    annualized_return_value: float | None,
    risk_free_rate_annual: float,
    downside_deviation_value: float | None,
) -> float | None:
    if annualized_return_value is None or not downside_deviation_value:
        return None
    return (annualized_return_value - risk_free_rate_annual) / downside_deviation_value


def beta(
    asset_series: Sequence[tuple[date, Decimal]],
    benchmark_series: Sequence[tuple[date, Decimal]],
) -> float | None:
    """Beta = Cov(retornos do ativo, retornos do benchmark) / Var(retornos
    do benchmark), usando apenas as datas em que ambas as séries têm
    cotação (ver ``daily_returns_by_date``)."""
    asset_returns = daily_returns_by_date(asset_series)
    benchmark_returns = daily_returns_by_date(benchmark_series)
    common_dates = sorted(set(asset_returns) & set(benchmark_returns))
    if len(common_dates) < 2:
        return None
    xs = [benchmark_returns[d] for d in common_dates]
    ys = [asset_returns[d] for d in common_dates]
    variance = statistics.variance(xs)
    if variance == 0:
        return None
    return statistics.covariance(xs, ys) / variance


def max_drawdown(values: Sequence[Decimal]) -> Decimal | None:
    """Maior queda percentual entre um pico e o vale seguinte
    (peak-to-trough), como fração ≤ 0 (ex.: ``Decimal("-0.23")`` = -23%).
    ``None`` com menos de 2 pontos."""
    if len(values) < 2:
        return None
    peak = values[0]
    worst = Decimal("0")
    for value in values[1:]:
        if value > peak:
            peak = value
        elif peak > 0:
            drawdown = (value - peak) / peak
            if drawdown < worst:
                worst = drawdown
    return worst


def portfolio_value_series(
    quantities: Mapping[Any, Decimal],
    price_series: Mapping[Any, Sequence[tuple[date, Decimal]]],
) -> list[tuple[date, Decimal]]:
    """Valor da carteira ao longo do tempo, para o drawdown por carteira
    inteira (item 4/Fase D).

    Aproximação documentada e combinada com o usuário: assume as
    quantidades ATUAIS de cada posição constantes durante todo o
    histórico — o modelo não sabe (nem tenta reconstruir) o tamanho das
    posições no passado, só o de hoje. Fica mais representativo à medida
    que ``quote_history`` acumular mais dias.

    Junta as séries de todos os tickers por "forward fill": em cada data
    presente na união das séries, usa o preço mais recente conhecido até
    aquela data para cada ticker. Só produz um ponto a partir da primeira
    data em que TODOS os tickers têm ao menos uma cotação — antes disso o
    valor ficaria artificialmente baixo pelos tickers ainda "zerados".
    """
    tickers = [key for key, quantity in quantities.items() if quantity != 0]
    if not tickers:
        return []
    sorted_series = {key: sorted(price_series.get(key, [])) for key in tickers}
    all_dates = sorted(
        {observed_date for series in sorted_series.values() for observed_date, _ in series}
    )
    cursors = {key: 0 for key in tickers}
    last_known: dict[Any, Decimal] = {}
    values: list[tuple[date, Decimal]] = []
    for current_date in all_dates:
        for key in tickers:
            series = sorted_series[key]
            cursor = cursors[key]
            while cursor < len(series) and series[cursor][0] <= current_date:
                last_known[key] = series[cursor][1]
                cursor += 1
            cursors[key] = cursor
        if len(last_known) < len(tickers):
            continue
        total = sum((quantities[key] * last_known[key] for key in tickers), Decimal("0"))
        values.append((current_date, total))
    return values


@dataclass(frozen=True, slots=True)
class TickerRiskMetrics:
    ticker: str
    currency: str
    observations: int
    """Número de retornos diários usados (nº de cotações históricas - 1)."""
    annualized_return: Decimal | None
    volatility_annualized: Decimal | None
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    var_95: Decimal | None
    max_drawdown: Decimal | None
    beta_vs_benchmark: Decimal | None

    @property
    def has_enough_observations(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS_FOR_CONFIDENCE


@dataclass(frozen=True, slots=True)
class PortfolioDrawdown:
    currency: str
    observations: int
    max_drawdown: Decimal | None

    @property
    def has_enough_observations(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS_FOR_CONFIDENCE


def build_ticker_risk_metrics(
    ticker: str,
    currency: str,
    series: Sequence[tuple[date, Decimal]],
    risk_free_rate_annual: Decimal,
    benchmark_series: Sequence[tuple[date, Decimal]] | None = None,
) -> TickerRiskMetrics:
    ordered = sorted(series)
    returns_by_date = daily_returns_by_date(ordered)
    returns = list(returns_by_date.values())
    prices = [price for _, price in ordered]

    volatility = annualized_volatility(returns)
    downside = downside_deviation(returns)
    var95 = historical_var(returns)
    ann_return = annualized_return_from_prices(ordered)
    risk_free = float(risk_free_rate_annual)
    sharpe = sharpe_ratio(ann_return, risk_free, volatility)
    sortino = sortino_ratio(ann_return, risk_free, downside)
    beta_value = beta(ordered, benchmark_series) if benchmark_series else None

    return TickerRiskMetrics(
        ticker=ticker,
        currency=currency,
        observations=len(returns),
        annualized_return=_to_decimal(ann_return) if ann_return is not None else None,
        volatility_annualized=_to_decimal(volatility) if volatility is not None else None,
        sharpe_ratio=_to_decimal(sharpe) if sharpe is not None else None,
        sortino_ratio=_to_decimal(sortino) if sortino is not None else None,
        var_95=_to_decimal(var95) if var95 is not None else None,
        max_drawdown=max_drawdown(prices),
        beta_vs_benchmark=_to_decimal(beta_value) if beta_value is not None else None,
    )


def build_portfolio_drawdown(
    currency: str,
    quantities: Mapping[Any, Decimal],
    price_series: Mapping[Any, Sequence[tuple[date, Decimal]]],
) -> PortfolioDrawdown:
    values = portfolio_value_series(quantities, price_series)
    prices = [value for _, value in values]
    return PortfolioDrawdown(
        currency=currency,
        observations=len(values),
        max_drawdown=max_drawdown(prices),
    )
