from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import OptionType
from app.options import _business_days, calculate_option

pytestmark = [pytest.mark.critical]


def _business_days_brute_force(start: date, end: date) -> int:
    if end <= start:
        return 0
    return sum(
        1
        for offset in range((end - start).days)
        if date.fromordinal(start.toordinal() + offset).weekday() < 5
    )


def test_business_days_matches_brute_force_reference_across_many_ranges() -> None:
    # Guards the O(1) rewrite against the original
    # day-by-day loop across every weekday start and a range of span lengths,
    # including spans shorter than a full week.
    base = date(2026, 1, 5)  # a Monday
    for start_offset in range(8):  # covers every weekday as a start day
        start = base + timedelta(days=start_offset)
        for span in range(0, 40):
            end = start + timedelta(days=span)
            assert _business_days(start, end) == _business_days_brute_force(start, end)


def test_business_days_returns_zero_for_non_positive_range() -> None:
    day = date(2026, 3, 10)
    assert _business_days(day, day) == 0
    assert _business_days(day, day - timedelta(days=5)) == 0


def test_call_metrics_match_trades_options_sheet() -> None:
    metrics = calculate_option(
        side="C",
        option_type=OptionType.CALL,
        quantity=Decimal("300"),
        average_cost=Decimal("3.84666666666667"),
        current_price=Decimal("9.67"),
        previous_close=Decimal("9.93"),
        strike=Decimal("9.06"),
        underlying_price=Decimal("18.69"),
        opened_on=date(2024, 8, 7),
        expiration_date=date(2026, 8, 21),
        result_mode="L",
        today=date(2026, 7, 27),
    )

    assert metrics.result == Decimal("1746.301199999999000400")
    assert metrics.breakeven == Decimal("12.90666666666667")
    assert metrics.strike_cushion == Decimal("9.63")
    assert metrics.breakeven_cushion == Decimal("5.78333333333333")
    assert metrics.business_days == 19


def test_put_uses_inverse_strike_and_breakeven_cushions() -> None:
    metrics = calculate_option(
        side="V",
        option_type=OptionType.PUT,
        quantity=Decimal("100"),
        average_cost=Decimal("2"),
        current_price=Decimal("1"),
        previous_close=Decimal("1.2"),
        strike=Decimal("10"),
        underlying_price=Decimal("8"),
        opened_on=date(2026, 7, 1),
        expiration_date=date(2026, 8, 21),
        result_mode="B",
        today=date(2026, 7, 27),
    )

    assert metrics.breakeven == Decimal("8")
    assert metrics.strike_cushion == Decimal("2")
    assert metrics.breakeven_cushion == Decimal("0")
    assert metrics.notional == Decimal("1000")
