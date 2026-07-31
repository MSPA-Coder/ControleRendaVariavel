from datetime import date
from decimal import Decimal

from app.monthly_performance import (
    MonthlyPerformanceReport,
    build_monthly_performance,
)


def test_build_monthly_performance_reduces_to_one_point_per_month() -> None:
    quantities = {"AAA": Decimal("10")}
    price_series = {
        "AAA": [
            (date(2026, 1, 5), Decimal("10")),
            (date(2026, 1, 20), Decimal("12")),  # último de janeiro
            (date(2026, 2, 3), Decimal("11")),
            (date(2026, 2, 25), Decimal("15")),  # último de fevereiro
        ]
    }

    report = build_monthly_performance("BRL", quantities, price_series)

    assert isinstance(report, MonthlyPerformanceReport)
    assert report.currency == "BRL"
    assert [(p.month, p.ending_value) for p in report.points] == [
        (date(2026, 1, 1), Decimal("120")),  # 10 * 12
        (date(2026, 2, 1), Decimal("150")),  # 10 * 15
    ]


def test_build_monthly_performance_computes_month_over_month_return() -> None:
    quantities = {"AAA": Decimal("1")}
    price_series = {
        "AAA": [
            (date(2026, 1, 31), Decimal("100")),
            (date(2026, 2, 28), Decimal("110")),
            (date(2026, 3, 31), Decimal("99")),
        ]
    }

    report = build_monthly_performance("BRL", quantities, price_series)

    assert report.points[0].return_pct is None  # primeiro mês da série
    assert report.points[1].return_pct == Decimal("10") / Decimal("100")
    assert report.points[2].return_pct == (Decimal("99") - Decimal("110")) / Decimal("110")


def test_build_monthly_performance_empty_without_positions() -> None:
    report = build_monthly_performance("BRL", {}, {})

    assert report.points == []


def test_build_monthly_performance_waits_for_all_tickers_like_portfolio_value_series() -> None:
    # Mesmo comportamento de forward-fill/espera documentado em
    # app.risk.portfolio_value_series: um ticker sem nenhuma cotação
    # ainda impede qualquer ponto de ser produzido.
    quantities = {"AAA": Decimal("1"), "BBB": Decimal("1")}
    price_series = {"AAA": [(date(2026, 1, 5), Decimal("10"))]}

    report = build_monthly_performance("BRL", quantities, price_series)

    assert report.points == []
