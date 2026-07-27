from datetime import date
from decimal import Decimal

from app.models import OptionType
from app.options import calculate_option


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
