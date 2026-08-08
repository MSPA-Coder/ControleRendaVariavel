import math
import statistics
from datetime import date
from decimal import Decimal

import pytest

from app.risk import (
    MIN_OBSERVATIONS_FOR_CONFIDENCE,
    PortfolioDrawdown,
    TickerRiskMetrics,
    annualized_return_from_prices,
    annualized_volatility,
    beta,
    build_portfolio_drawdown,
    build_ticker_risk_metrics,
    daily_returns_by_date,
    downside_deviation,
    historical_var,
    max_drawdown,
    portfolio_value_series,
    sharpe_ratio,
    sortino_ratio,
)

pytestmark = [pytest.mark.critical]

ASSET_PRICES = [
    (date(2026, 1, 1), Decimal("100")),
    (date(2026, 1, 2), Decimal("101.00")),
    (date(2026, 1, 3), Decimal("103.0200")),
    (date(2026, 1, 4), Decimal("101.989800")),
    (date(2026, 1, 5), Decimal("105.04949400")),
]
"""Preços construídos para gerar exatamente os retornos diários
[0.01, 0.02, -0.01, 0.03] (ver comentário de geração no handoff)."""

BENCHMARK_PRICES = [
    (date(2026, 1, 1), Decimal("50")),
    (date(2026, 1, 2), Decimal("50.250")),
    (date(2026, 1, 3), Decimal("51.003750")),
    (date(2026, 1, 4), Decimal("50.748731250")),
    (date(2026, 1, 5), Decimal("51.76370587500")),
]
"""Gera os retornos [0.005, 0.015, -0.005, 0.02]."""


def test_daily_returns_by_date_computes_simple_returns() -> None:
    returns = daily_returns_by_date(ASSET_PRICES)

    assert list(returns.keys()) == [
        date(2026, 1, 2),
        date(2026, 1, 3),
        date(2026, 1, 4),
        date(2026, 1, 5),
    ]
    assert returns[date(2026, 1, 2)] == pytest.approx(0.01)
    assert returns[date(2026, 1, 3)] == pytest.approx(0.02)
    assert returns[date(2026, 1, 4)] == pytest.approx(-0.01)
    assert returns[date(2026, 1, 5)] == pytest.approx(0.03)


def test_annualized_volatility_matches_manual_calculation() -> None:
    returns = list(daily_returns_by_date(ASSET_PRICES).values())

    expected = statistics.stdev(returns) * math.sqrt(252)
    assert annualized_volatility(returns) == pytest.approx(expected)


def test_annualized_volatility_none_with_fewer_than_two_returns() -> None:
    assert annualized_volatility([0.01]) is None
    assert annualized_volatility([]) is None


def test_downside_deviation_only_considers_negative_returns() -> None:
    returns = [0.02, -0.01, 0.03, -0.02]

    expected = math.sqrt(((-0.01) ** 2 + (-0.02) ** 2) / 4) * math.sqrt(252)
    assert downside_deviation(returns) == pytest.approx(expected)


def test_downside_deviation_is_zero_when_no_losses() -> None:
    assert downside_deviation([0.01, 0.02, 0.03]) == 0.0


def test_historical_var_is_the_lower_percentile_of_returns() -> None:
    # 20 observações "limpas": -0.10, -0.09, ..., 0.09 (passo 0.01)
    returns = [round(-0.10 + 0.01 * i, 2) for i in range(20)]

    # position = (1-0.95)*(20-1) = 0.95 -> interpola entre ordered[0]=-0.10
    # e ordered[1]=-0.09 com peso 0.95: -0.10*0.05 + -0.09*0.95 = -0.0905
    var95 = historical_var(returns, confidence=0.95)
    assert var95 is not None
    assert var95 == pytest.approx(-0.0905, abs=1e-9)


def test_historical_var_none_with_fewer_than_two_returns() -> None:
    assert historical_var([0.01]) is None


def test_annualized_return_from_prices_is_cagr() -> None:
    series = [
        (date(2026, 1, 1), Decimal("100")),
        (date(2027, 1, 1), Decimal("110")),
    ]

    result = annualized_return_from_prices(series)

    assert result is not None
    assert result == pytest.approx(0.10, abs=0.001)


def test_annualized_return_from_prices_none_with_single_point() -> None:
    assert annualized_return_from_prices([(date(2026, 1, 1), Decimal("100"))]) is None


def test_sharpe_and_sortino_ratio_none_without_inputs() -> None:
    assert sharpe_ratio(None, 0.1, 0.2) is None
    assert sharpe_ratio(0.2, 0.1, None) is None
    assert sharpe_ratio(0.2, 0.1, 0.0) is None
    assert sortino_ratio(0.2, 0.1, None) is None


def test_sharpe_ratio_matches_manual_formula() -> None:
    assert sharpe_ratio(0.25, 0.10, 0.30) == pytest.approx((0.25 - 0.10) / 0.30)


def test_beta_matches_manual_covariance_over_variance() -> None:
    result = beta(ASSET_PRICES, BENCHMARK_PRICES)

    xs = list(daily_returns_by_date(BENCHMARK_PRICES).values())
    ys = list(daily_returns_by_date(ASSET_PRICES).values())
    expected = statistics.covariance(xs, ys) / statistics.variance(xs)

    assert result == pytest.approx(expected)


def test_beta_none_with_fewer_than_two_common_dates() -> None:
    short_benchmark = BENCHMARK_PRICES[:2]
    assert beta(ASSET_PRICES, short_benchmark) is None


def test_beta_none_without_overlapping_dates() -> None:
    other_dates = [
        (date(2030, 1, 1), Decimal("10")),
        (date(2030, 1, 2), Decimal("11")),
    ]
    assert beta(ASSET_PRICES, other_dates) is None


def test_max_drawdown_tracks_peak_to_trough() -> None:
    values = [Decimal("100"), Decimal("120"), Decimal("90"), Decimal("110"), Decimal("80")]

    # Pico 120 -> vale 80: (80 - 120) / 120
    assert max_drawdown(values) == (Decimal("80") - Decimal("120")) / Decimal("120")


def test_max_drawdown_is_zero_when_always_rising() -> None:
    values = [Decimal("100"), Decimal("110"), Decimal("120")]
    assert max_drawdown(values) == Decimal("0")


def test_max_drawdown_none_with_fewer_than_two_points() -> None:
    assert max_drawdown([Decimal("100")]) is None
    assert max_drawdown([]) is None


def test_portfolio_value_series_forward_fills_and_waits_for_all_tickers() -> None:
    quantities = {"AAA": Decimal("10"), "BBB": Decimal("5")}
    price_series = {
        "AAA": [
            (date(2026, 1, 1), Decimal("10")),
            (date(2026, 1, 3), Decimal("12")),
        ],
        "BBB": [
            (date(2026, 1, 2), Decimal("20")),
            (date(2026, 1, 3), Decimal("22")),
        ],
    }

    values = portfolio_value_series(quantities, price_series)

    # 1º de jan: só AAA tem cotação -> descartado (falta BBB).
    # 2 de jan: AAA usa o último preço conhecido (10), BBB tem 20.
    #   10*10 + 5*20 = 100 + 100 = 200
    # 3 de jan: AAA=12, BBB=22 -> 10*12 + 5*22 = 120 + 110 = 230
    assert values == [
        (date(2026, 1, 2), Decimal("200")),
        (date(2026, 1, 3), Decimal("230")),
    ]


def test_portfolio_value_series_ignores_zero_quantity_tickers() -> None:
    quantities = {"AAA": Decimal("0")}
    price_series = {"AAA": [(date(2026, 1, 1), Decimal("10"))]}

    assert portfolio_value_series(quantities, price_series) == []


def test_portfolio_value_series_empty_without_positions() -> None:
    assert portfolio_value_series({}, {}) == []


def test_build_ticker_risk_metrics_wires_all_kpis_together() -> None:
    metrics = build_ticker_risk_metrics(
        ticker="PETR4",
        currency="BRL",
        series=ASSET_PRICES,
        risk_free_rate_annual=Decimal("0.10"),
        benchmark_series=BENCHMARK_PRICES,
    )

    assert isinstance(metrics, TickerRiskMetrics)
    assert metrics.ticker == "PETR4"
    assert metrics.observations == 4
    assert metrics.volatility_annualized is not None
    assert metrics.sharpe_ratio is not None
    assert metrics.sortino_ratio is not None
    assert metrics.var_95 is not None
    assert metrics.max_drawdown is not None
    assert metrics.beta_vs_benchmark is not None
    assert metrics.has_enough_observations is False  # 4 < MIN_OBSERVATIONS_FOR_CONFIDENCE


def test_build_ticker_risk_metrics_beta_is_none_without_benchmark() -> None:
    metrics = build_ticker_risk_metrics(
        ticker="PETR4",
        currency="BRL",
        series=ASSET_PRICES,
        risk_free_rate_annual=Decimal("0.10"),
        benchmark_series=None,
    )

    assert metrics.beta_vs_benchmark is None


def test_build_ticker_risk_metrics_does_not_crash_on_short_window_extrapolation() -> None:
    # Regressão: 2 cotações separadas por 1 dia com queda de 20% ->
    # annualized_return_from_prices projeta para 365 dias e produz um
    # número absurdamente grande (~1e28), que antes estourava a precisão
    # do Decimal.quantize e derrubava a página de risco com 500.
    series = [
        (date(2026, 1, 1), Decimal("100")),
        (date(2026, 1, 2), Decimal("80")),
    ]

    metrics = build_ticker_risk_metrics(
        ticker="PETR4",
        currency="BRL",
        series=series,
        risk_free_rate_annual=Decimal("0.10"),
    )

    assert metrics.annualized_return is None
    assert metrics.max_drawdown == (Decimal("80") - Decimal("100")) / Decimal("100")


def test_has_enough_observations_threshold() -> None:
    assert MIN_OBSERVATIONS_FOR_CONFIDENCE == 20
    below = TickerRiskMetrics("X", "BRL", 19, None, None, None, None, None, None, None)
    at = TickerRiskMetrics("X", "BRL", 20, None, None, None, None, None, None, None)
    assert below.has_enough_observations is False
    assert at.has_enough_observations is True


def test_build_portfolio_drawdown() -> None:
    quantities = {"AAA": Decimal("10")}
    price_series = {
        "AAA": [
            (date(2026, 1, 1), Decimal("100")),
            (date(2026, 1, 2), Decimal("80")),
        ]
    }

    result = build_portfolio_drawdown("BRL", quantities, price_series)

    assert isinstance(result, PortfolioDrawdown)
    assert result.currency == "BRL"
    assert result.observations == 2
    assert result.max_drawdown == (Decimal("800") - Decimal("1000")) / Decimal("1000")
