from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.models import OptionPosition
from app.options import OptionMetrics, calculate_option


@dataclass(frozen=True, slots=True)
class OptionView:
    position: OptionPosition
    metrics: OptionMetrics | None
    quote_status: str


@dataclass(frozen=True, slots=True)
class ExpirationTotal:
    expiration_date: date
    provisional_notional: Decimal
    notional: Decimal
    unwind: Decimal
    result: Decimal


@dataclass(frozen=True, slots=True)
class OptionPortfolio:
    positions: list[OptionView]
    gain: Decimal
    loss: Decimal
    result: Decimal
    expirations: list[ExpirationTotal]


def build_option_portfolio(
    positions: list[OptionPosition],
    *,
    stale_after_seconds: int,
    today: date | None = None,
    now: datetime | None = None,
) -> OptionPortfolio:
    current_date = today or date.today()
    observed_now = now or datetime.now(UTC)
    views: list[OptionView] = []
    for position in positions:
        quote = position.quote
        if quote is None:
            views.append(OptionView(position, None, "missing"))
            continue
        status = quote.source_status
        if observed_now - quote.observed_at > timedelta(seconds=stale_after_seconds):
            status = "stale"
        position_metrics = calculate_option(
            side=position.side.value,
            option_type=position.contract.option_type,
            quantity=position.quantity,
            average_cost=position.average_cost,
            current_price=quote.last_price,
            previous_close=quote.previous_close,
            strike=position.contract.strike,
            underlying_price=quote.underlying_price,
            opened_on=position.opened_on,
            expiration_date=position.contract.expiration.exercise_date,
            result_mode=position.result_mode,
            today=current_date,
        )
        views.append(OptionView(position, position_metrics, status))

    all_metrics = [view.metrics for view in views if view.metrics is not None]
    grouped: dict[date, list[OptionMetrics]] = {}
    for view in views:
        if view.metrics is not None:
            expiration = view.position.contract.expiration.exercise_date
            grouped.setdefault(expiration, []).append(view.metrics)
    expirations = [
        ExpirationTotal(
            expiration,
            sum(
                (
                    metric.notional
                    for metric in group
                    if metric.strike_cushion < 0
                ),
                Decimal("0"),
            ),
            sum((metric.notional for metric in group), Decimal("0")),
            sum((metric.unwind_value for metric in group), Decimal("0")),
            sum((metric.result for metric in group), Decimal("0")),
        )
        for expiration, group in sorted(grouped.items())
    ]
    gain = sum(
        (metric.result for metric in all_metrics if metric.result > 0), Decimal("0")
    )
    loss = sum(
        (metric.result for metric in all_metrics if metric.result < 0), Decimal("0")
    )
    return OptionPortfolio(views, gain, loss, gain + loss, expirations)
