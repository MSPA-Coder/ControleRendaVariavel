from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain import PositionMetrics, calculate_position, safe_div
from app.models import Market, Position


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
    return_pct: Decimal | None
    """Σ resultado / Σ custo desta moeda.

    Deliberadamente calculado por moeda, nunca somando moedas diferentes:
    o restante do módulo (pesos, totais) já segrega tudo por moeda porque
    somar BRL e USD sem uma taxa de câmbio produziria um número sem
    significado financeiro real.
    """
    hhi: Decimal | None
    """Índice Herfindahl-Hirschman (Σ peso_i²) desta moeda: quanto menor,
    mais diversificada a carteira nessa moeda. Usa o peso pelo valor atual
    (current_weight) de cada posição cotada."""


@dataclass(frozen=True, slots=True)
class BrokerGroup:
    broker: str
    currency: str
    positions: list[PositionView]
    current_total: Decimal
    cost_total: Decimal
    result_total: Decimal
    current_weight: Decimal | None
    """Participação desta corretora no valor atual total da mesma moeda."""
    cost_weight: Decimal | None
    """Participação desta corretora no custo total da mesma moeda."""


@dataclass(frozen=True, slots=True)
class MarketGroup:
    market: Market
    currency: str
    positions: list[PositionView]
    current_total: Decimal
    cost_total: Decimal
    result_total: Decimal
    current_weight: Decimal | None
    """Participação deste mercado (B3/NYSE/NASDAQ) no valor atual total da
    mesma moeda."""
    cost_weight: Decimal | None
    """Participação deste mercado no custo total da mesma moeda."""


@dataclass(frozen=True, slots=True)
class PortfolioView:
    positions: list[PositionView]
    currency_totals: list[PortfolioTotal]
    broker_groups: list[BrokerGroup]
    market_groups: list[MarketGroup]


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
    market_grouped: dict[tuple[Market, str], list[PositionView]] = {}
    for view in views:
        grouped.setdefault((view.position.broker, view.position.currency), []).append(view)
        market_grouped.setdefault((view.position.market, view.position.currency), []).append(view)

    def _aggregate(
        group_views: list[PositionView], currency: str
    ) -> tuple[Decimal, Decimal, Decimal, Decimal | None, Decimal | None]:
        group_metrics = [view.metrics for view in group_views if view.metrics is not None]
        current_total = sum((abs(metric.unwind_value) for metric in group_metrics), Decimal("0"))
        cost_total = sum((abs(metric.build_value) for metric in group_metrics), Decimal("0"))
        result_total = sum((metric.result for metric in group_metrics), Decimal("0"))
        currency_current_total, currency_cost_total, _ = totals_by_currency[currency]
        return (
            current_total,
            cost_total,
            result_total,
            safe_div(current_total, currency_current_total),
            safe_div(cost_total, currency_cost_total),
        )

    broker_groups = [
        BrokerGroup(broker, currency, group_views, *_aggregate(group_views, currency))
        for (broker, currency), group_views in grouped.items()
    ]
    market_groups = [
        MarketGroup(market, currency, group_views, *_aggregate(group_views, currency))
        for (market, currency), group_views in market_grouped.items()
    ]

    currency_total_views = []
    for currency, (current_total, cost_total, result_total) in totals_by_currency.items():
        weights = [
            view.current_weight
            for view in views
            if view.position.currency == currency and view.current_weight is not None
        ]
        hhi = sum((weight * weight for weight in weights), Decimal("0")) if weights else None
        currency_total_views.append(
            PortfolioTotal(
                currency=currency,
                current_total=current_total,
                cost_total=cost_total,
                result_total=result_total,
                return_pct=safe_div(result_total, cost_total),
                hhi=hhi,
            )
        )
    return PortfolioView(views, currency_total_views, broker_groups, market_groups)
