from datetime import date
from decimal import Decimal

from app.monthly_performance import (
    MonthlyPerformanceReport,
    build_monthly_performance,
    normalize_performance_period,
    select_performance_period,
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


def test_build_monthly_performance_uses_available_quote_history() -> None:
    # Mesmo comportamento de forward-fill/espera documentado em
    # app.risk.portfolio_value_series: um ticker sem nenhuma cotação
    # ainda impede qualquer ponto de ser produzido.
    quantities = {"AAA": Decimal("1"), "BBB": Decimal("1")}
    price_series = {"AAA": [(date(2026, 1, 5), Decimal("10"))]}

    report = build_monthly_performance("BRL", quantities, price_series)

    assert [(point.month, point.ending_value) for point in report.points] == [
        (date(2026, 1, 1), Decimal("10"))
    ]


def test_build_monthly_performance_includes_history_before_every_ticker_has_quotes() -> None:
    report = build_monthly_performance(
        "BRL",
        {"AAA": Decimal("1"), "BBB": Decimal("2")},
        {
            "AAA": [(date(2022, 1, 31), Decimal("10"))],
            "BBB": [(date(2026, 1, 31), Decimal("20"))],
        },
    )

    assert [(point.month, point.ending_value) for point in report.points] == [
        (date(2022, 1, 1), Decimal("10")),
        (date(2026, 1, 1), Decimal("50")),
    ]


def test_select_performance_period_uses_last_quote_as_its_reference() -> None:
    values = [
        (date(2025, 8, 1), Decimal("100")),
        (date(2026, 2, 2), Decimal("110")),
        (date(2026, 8, 2), Decimal("120")),
    ]

    assert select_performance_period(values, "semester") == values[1:]
    assert select_performance_period(values, "all") == values


def test_build_monthly_performance_applies_the_period_before_monthly_reduction() -> None:
    report = build_monthly_performance(
        "BRL",
        {"AAA": Decimal("1")},
        {
            "AAA": [
                (date(2026, 7, 31), Decimal("100")),
                (date(2026, 8, 8), Decimal("120")),
            ]
        },
        period="week",
    )

    assert [(point.month, point.ending_value) for point in report.points] == [
        (date(2026, 8, 1), Decimal("120"))
    ]


def test_normalize_performance_period_defaults_to_all() -> None:
    assert normalize_performance_period(None) == "all"
    assert normalize_performance_period("invalid") == "all"
    values = [(date(2026, 1, 1), Decimal("100"))]
    assert select_performance_period(values, "invalid") == values
