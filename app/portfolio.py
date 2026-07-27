from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain import PositionMetrics, calculate_position
from app.models import Position


@dataclass(frozen=True, slots=True)
class PositionView:
    position: Position
    metrics: PositionMetrics | None
    current_weight: Decimal | None
    cost_weight: Decimal | None
    quote_status: str


@dataclass(frozen=True, slots=True)
class PortfolioTotal:
    currency: str
    current_total: Decimal
    cost_total: Decimal
    result_total: Decimal


@dataclass(frozen=True, slots=True)
class BrokerGroup:
    broker: str
    currency: str
    positions: list[PositionView]
    current_total: Decimal
    cost_total: Decimal
    result_total: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioView:
    positions: list[PositionView]
    currency_totals: list[PortfolioTotal]
    broker_groups: list[BrokerGroup]


def build_portfolio(
    positions: list[Position],
    *,
    stale_after_seconds: int,
    today: date | None = None,
    now: datetime | None = None,
) -> PortfolioView:
    observed_now = now or datetime.now(UTC)
    calculated: list[tuple[Position, PositionMetrics | None, str]] = []
    totals_by_currency: dict[str, list[Decimal]] = {}

    for position in positions:
        currency_totals = totals_by_currency.setdefault(
            position.currency, [Decimal("0"), Decimal("0"), Decimal("0")]
        )
        quote = position.quote
        if quote is None:
            calculated.append((position, None, "missing"))
            continue
        status = quote.source_status
        if observed_now - quote.observed_at > timedelta(seconds=stale_after_seconds):
            status = "stale"
        metrics = calculate_position(
            side=position.side.value,
            quantity=position.quantity,
            average_cost=position.average_cost,
            raw_price=quote.last_price,
            previous_close=quote.previous_close,
            quote_multiplier=position.quote_multiplier,
            target_multiplier=position.target_multiplier,
            opened_on=position.opened_on,
            result_mode=position.result_mode,
            today=today,
        )
        currency_totals[0] += abs(metrics.unwind_value)
        currency_totals[1] += abs(metrics.build_value)
        currency_totals[2] += metrics.result
        calculated.append((position, metrics, status))

    views = [
        PositionView(
            position=position,
            metrics=metrics,
            current_weight=(
                abs(metrics.unwind_value) / totals_by_currency[position.currency][0]
                if metrics is not None and totals_by_currency[position.currency][0]
                else None
            ),
            cost_weight=(
                abs(metrics.build_value) / totals_by_currency[position.currency][1]
                if metrics is not None and totals_by_currency[position.currency][1]
                else None
            ),
            quote_status=status,
        )
        for position, metrics, status in calculated
    ]
    grouped: dict[tuple[str, str], list[PositionView]] = {}
    for view in views:
        key = (view.position.broker, view.position.currency)
        grouped.setdefault(key, []).append(view)

    broker_groups = []
    for (broker, currency), group_views in grouped.items():
        group_metrics = [view.metrics for view in group_views if view.metrics is not None]
        broker_groups.append(
            BrokerGroup(
                broker=broker,
                currency=currency,
                positions=group_views,
                current_total=sum(
                    (abs(metric.unwind_value) for metric in group_metrics), Decimal("0")
                ),
                cost_total=sum(
                    (abs(metric.build_value) for metric in group_metrics), Decimal("0")
                ),
                result_total=sum((metric.result for metric in group_metrics), Decimal("0")),
            )
        )

    currency_total_views = [
        PortfolioTotal(currency, values[0], values[1], values[2])
        for currency, values in totals_by_currency.items()
    ]
    return PortfolioView(views, currency_total_views, broker_groups)
