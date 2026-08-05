from datetime import date
from decimal import Decimal

import pytest

from app.monthly_performance import (
    MonthlyPerformanceReport,
    align_benchmark_to_points,
    build_benchmark_shadow_series,
    build_monthly_performance,
    normalize_performance_period,
    select_performance_period,
)

pytestmark = [pytest.mark.critical, pytest.mark.business_rule]


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


def test_align_benchmark_to_points_uses_last_value_within_each_month() -> None:
    report = build_monthly_performance(
        "BRL",
        {"AAA": Decimal("1")},
        {
            "AAA": [
                (date(2026, 1, 31), Decimal("100")),
                (date(2026, 2, 28), Decimal("110")),
            ]
        },
    )
    benchmark_series = [
        (date(2026, 1, 5), Decimal("50")),
        (date(2026, 1, 20), Decimal("55")),  # último de janeiro
        (date(2026, 2, 3), Decimal("60")),
        (date(2026, 2, 25), Decimal("58")),  # último de fevereiro
    ]

    assert align_benchmark_to_points(report.points, benchmark_series) == [
        Decimal("55"),
        Decimal("58"),
    ]


def test_align_benchmark_to_points_leaves_missing_months_as_none() -> None:
    report = build_monthly_performance(
        "BRL",
        {"AAA": Decimal("1")},
        {
            "AAA": [
                (date(2026, 1, 31), Decimal("100")),
                (date(2026, 2, 28), Decimal("110")),
                (date(2026, 3, 31), Decimal("120")),
            ]
        },
    )
    # Sem nenhuma cotação do índice em fevereiro: o buraco é preservado
    # (None), nunca interpolado, mas a posição na lista continua
    # correspondendo ao mês de `report.points`.
    benchmark_series = [
        (date(2026, 1, 15), Decimal("50")),
        (date(2026, 3, 10), Decimal("70")),
    ]

    assert align_benchmark_to_points(report.points, benchmark_series) == [
        Decimal("50"),
        None,
        Decimal("70"),
    ]


def test_align_benchmark_to_points_empty_series_is_all_none() -> None:
    report = build_monthly_performance(
        "BRL", {"AAA": Decimal("1")}, {"AAA": [(date(2026, 1, 31), Decimal("100"))]}
    )

    assert align_benchmark_to_points(report.points, []) == [None]


def test_build_benchmark_shadow_series_grows_with_the_benchmark_since_entry() -> None:
    # R$1.000 aplicados no dia da abertura (preço 100): dobra de valor junto
    # com o benchmark.
    contributions = [(date(2026, 1, 1), Decimal("1000"))]
    benchmark_series = [
        (date(2026, 1, 1), Decimal("100")),
        (date(2026, 2, 1), Decimal("150")),
        (date(2026, 3, 1), Decimal("200")),
    ]

    result = build_benchmark_shadow_series(contributions, benchmark_series)

    assert result == [
        (date(2026, 1, 1), Decimal("1000")),
        (date(2026, 2, 1), Decimal("1500")),
        (date(2026, 3, 1), Decimal("2000")),
    ]


def test_build_benchmark_shadow_series_each_contribution_only_counts_from_its_own_date() -> None:
    # Este é o caso que motivou a mudança: uma segunda compra (aporte) não
    # pode fazer a curva do benchmark saltar antes da sua própria data, ou a
    # comparação com uma carteira que recebe aportes some sentido (ver
    # docstring da função). Compra 1: R$1.000 em 01/01. Compra 2 (outro
    # ativo): R$500 em 01/03.
    contributions = [
        (date(2026, 1, 1), Decimal("1000")),
        (date(2026, 3, 1), Decimal("500")),
    ]
    benchmark_series = [
        (date(2026, 1, 1), Decimal("100")),
        (date(2026, 2, 1), Decimal("110")),
        (date(2026, 3, 1), Decimal("105")),
        (date(2026, 4, 1), Decimal("120")),
    ]

    result = build_benchmark_shadow_series(contributions, benchmark_series)

    assert result == [
        (date(2026, 1, 1), Decimal("1000")),  # só a 1ª compra
        (date(2026, 2, 1), Decimal("1100")),  # só a 1ª compra, benchmark subiu 10%
        # a partir daqui as duas contam: 1000*105/100 + 500*105/105
        (date(2026, 3, 1), Decimal("1550")),
        (date(2026, 4, 1), Decimal("1200") + Decimal("500") * Decimal("120") / Decimal("105")),
    ]


def test_build_benchmark_shadow_series_anchors_on_first_price_at_or_after_entry() -> None:
    # Sem cotação do benchmark exatamente na data de abertura (fim de
    # semana/feriado): ancora no primeiro preço disponível a partir dali,
    # não antes (não faria sentido "investir" num preço anterior à compra).
    contributions = [(date(2026, 1, 10), Decimal("1000"))]
    benchmark_series = [
        (date(2026, 1, 5), Decimal("90")),  # antes da abertura, ignorado como âncora
        (date(2026, 1, 15), Decimal("100")),  # primeiro preço >= 10/01
        (date(2026, 2, 1), Decimal("110")),
    ]

    result = build_benchmark_shadow_series(contributions, benchmark_series)

    assert result == [
        (date(2026, 1, 5), Decimal("0")),  # antes da abertura: contribuição zero
        (date(2026, 1, 15), Decimal("1000")),
        (date(2026, 2, 1), Decimal("1100")),
    ]


def test_build_benchmark_shadow_series_ignores_non_positive_contributions() -> None:
    contributions = [(date(2026, 1, 1), Decimal("0")), (date(2026, 1, 1), Decimal("-5"))]
    benchmark_series = [(date(2026, 1, 1), Decimal("100"))]

    assert build_benchmark_shadow_series(contributions, benchmark_series) == []


def test_build_benchmark_shadow_series_empty_without_benchmark_history() -> None:
    contributions = [(date(2026, 1, 1), Decimal("1000"))]

    assert build_benchmark_shadow_series(contributions, []) == []
