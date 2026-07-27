from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

ZERO = Decimal("0")
ONE = Decimal("1")
LIQUID_FACTOR = Decimal("0.9996")
MONEY_QUANT = Decimal("0.01")


def q_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def safe_div(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return None if denominator == ZERO else numerator / denominator


def operation_result(
    side: str,
    quantity: Decimal,
    average_cost: Decimal,
    current_price: Decimal,
    result_mode: str,
) -> Decimal:
    """Equivalent to ResultadoOperacao for the arguments used by sheet Ações."""
    direction = ONE if side == "C" else -ONE
    gross = direction * quantity * (current_price - average_cost)
    return gross * LIQUID_FACTOR if result_mode == "L" else gross


def signed_annualized_return(total_return: Decimal | None, days: int) -> Decimal | None:
    if total_return is None or days <= 0:
        return None
    sign = -ONE if total_return < ZERO else ONE
    base = ONE + abs(total_return)
    with localcontext() as context:
        context.prec = 28
        exponent = Decimal(365) / Decimal(days)
        return sign * (base**exponent - ONE)


@dataclass(frozen=True, slots=True)
class PositionMetrics:
    days: int
    current_price: Decimal
    daily_variation: Decimal | None
    result: Decimal
    return_pct: Decimal | None
    annualized_return: Decimal | None
    stop_gain: Decimal
    distance_to_target: Decimal | None
    breakeven: Decimal | None
    unwind_value: Decimal
    build_value: Decimal


def calculate_position(
    *,
    side: str,
    quantity: Decimal,
    average_cost: Decimal,
    raw_price: Decimal,
    previous_close: Decimal,
    quote_multiplier: Decimal,
    target_multiplier: Decimal,
    opened_on: date,
    result_mode: str,
    today: date | None = None,
) -> PositionMetrics:
    current = raw_price * quote_multiplier
    previous = previous_close * quote_multiplier
    direction = ONE if side == "C" else -ONE
    result = operation_result(side, quantity, average_cost, current, result_mode)
    invested = quantity * average_cost
    return_pct = safe_div(result, invested)
    days = ((today or date.today()) - opened_on).days
    stop_gain = average_cost * target_multiplier
    distance_to_target = safe_div(stop_gain, current)
    if distance_to_target is not None:
        distance_to_target -= ONE
    breakeven = (
        safe_div(current, average_cost)
        if average_cost < current
        else safe_div(average_cost, current)
    )
    if breakeven is not None:
        breakeven = breakeven - ONE if average_cost < current else -(breakeven - ONE)
    daily = safe_div(previous, current)
    if daily is not None:
        daily = direction * (ONE - daily)
    return PositionMetrics(
        days=days,
        current_price=current,
        daily_variation=daily,
        result=result,
        return_pct=return_pct,
        annualized_return=signed_annualized_return(return_pct, days),
        stop_gain=stop_gain,
        distance_to_target=distance_to_target,
        breakeven=breakeven,
        unwind_value=direction * quantity * current,
        build_value=-direction * quantity * average_cost,
    )
