from datetime import date
from decimal import Decimal

import pytest

from app.domain import calculate_position, operation_result, safe_div

pytestmark = [pytest.mark.critical, pytest.mark.business_rule]


@pytest.mark.parametrize(
    ("side", "mode", "expected"),
    [
        ("C", "L", Decimal("199.9200")),
        ("V", "L", Decimal("-199.9200")),
        ("C", "B", Decimal("200")),
    ],
)
def test_resultado_operacao_matches_workbook(side: str, mode: str, expected: Decimal) -> None:
    assert operation_result(side, Decimal("100"), Decimal("10"), Decimal("12"), mode) == expected


def test_sheet_metrics_are_reproduced() -> None:
    metrics = calculate_position(
        side="C",
        quantity=Decimal("100"),
        average_cost=Decimal("10"),
        raw_price=Decimal("12"),
        previous_close=Decimal("11"),
        quote_multiplier=Decimal("1"),
        target_multiplier=Decimal("1.5"),
        opened_on=date(2025, 1, 1),
        result_mode="L",
        today=date(2025, 4, 11),
    )
    assert metrics.days == 100
    assert metrics.result == Decimal("199.9200")
    assert metrics.return_pct == Decimal("0.19992")
    assert metrics.stop_gain == Decimal("15.0")
    assert metrics.distance_to_target == Decimal("0.25")
    assert metrics.breakeven == Decimal("0.2")
    assert metrics.unwind_value == Decimal("1200")
    assert metrics.build_value == Decimal("-1000")
    assert metrics.daily_variation == Decimal("1") - Decimal("11") / Decimal("12")


def test_zero_denominator_is_not_an_error() -> None:
    assert safe_div(Decimal("1"), Decimal("0")) is None
