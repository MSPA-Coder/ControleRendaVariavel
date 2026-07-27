from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain import operation_result, safe_div
from app.models import OptionType


@dataclass(frozen=True, slots=True)
class OptionMetrics:
    current_price: Decimal
    daily_variation: Decimal | None
    total_variation: Decimal | None
    result: Decimal
    return_pct: Decimal | None
    elapsed_days: int
    remaining_days: int
    elapsed_pct: Decimal | None
    business_days: int
    unwind_value: Decimal
    build_value: Decimal
    breakeven: Decimal
    strike_cushion: Decimal
    strike_cushion_pct: Decimal | None
    breakeven_cushion: Decimal
    breakeven_cushion_pct: Decimal | None
    notional: Decimal


def _business_days(start: date, end: date) -> int:
    if end <= start:
        return 0
    return sum(
        1
        for offset in range((end - start).days)
        if date.fromordinal(start.toordinal() + offset).weekday() < 5
    )


def calculate_option(
    *,
    side: str,
    option_type: OptionType,
    quantity: Decimal,
    average_cost: Decimal,
    current_price: Decimal,
    previous_close: Decimal,
    strike: Decimal,
    underlying_price: Decimal,
    opened_on: date,
    expiration_date: date,
    result_mode: str,
    today: date,
) -> OptionMetrics:
    direction = Decimal("1") if side == "C" else Decimal("-1")
    elapsed_days = max((today - opened_on).days, 0)
    remaining_days = max((expiration_date - today).days, 0)
    total_days = max((expiration_date - opened_on).days, 0)
    result = operation_result(
        side, quantity, average_cost, current_price, result_mode
    )
    daily = (
        direction * (Decimal("1") - previous_close / current_price)
        if current_price
        else None
    )
    total_variation = (
        safe_div(average_cost, current_price)
        if side == "V"
        else safe_div(current_price, average_cost)
    )
    if total_variation is not None:
        total_variation -= Decimal("1")
    if option_type == OptionType.CALL:
        breakeven = strike + average_cost
        strike_cushion = underlying_price - strike
        strike_cushion_pct = (
            Decimal("1") - strike / underlying_price if underlying_price else None
        )
        breakeven_cushion = underlying_price - breakeven
        breakeven_cushion_pct = (
            Decimal("1") - breakeven / underlying_price
            if underlying_price
            else None
        )
    else:
        breakeven = strike - average_cost
        strike_cushion = strike - underlying_price
        strike_cushion_pct = (
            Decimal("1") - underlying_price / strike if strike else None
        )
        breakeven_cushion = breakeven - underlying_price
        breakeven_cushion_pct = (
            Decimal("1") - underlying_price / breakeven if breakeven else None
        )
    return OptionMetrics(
        current_price=current_price,
        daily_variation=daily,
        total_variation=total_variation,
        result=result,
        return_pct=safe_div(result, quantity * average_cost),
        elapsed_days=elapsed_days,
        remaining_days=remaining_days,
        elapsed_pct=safe_div(Decimal(elapsed_days), Decimal(total_days)),
        business_days=_business_days(today, expiration_date),
        unwind_value=direction * quantity * current_price,
        build_value=-direction * quantity * average_cost,
        breakeven=breakeven,
        strike_cushion=strike_cushion,
        strike_cushion_pct=strike_cushion_pct,
        breakeven_cushion=breakeven_cushion,
        breakeven_cushion_pct=breakeven_cushion_pct,
        notional=quantity * strike if side == "V" else Decimal("0"),
    )
