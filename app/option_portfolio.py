from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.greeks import OptionGreeks, calculate_greeks
from app.models import OptionPosition
from app.options import OptionMetrics, calculate_option


@dataclass(frozen=True, slots=True)
class OptionView:
    position: OptionPosition
    metrics: OptionMetrics | None
    quote_status: str
    greeks: OptionGreeks | None


@dataclass(frozen=True, slots=True)
class ExpirationTotal:
    expiration_date: date
    provisional_notional: Decimal
    notional: Decimal
    unwind: Decimal
    result: Decimal


@dataclass(frozen=True, slots=True)
class MoneynessTotal:
    moneyness: str
    """"ITM", "ATM" ou "OTM"."""
    count: int
    pct: Decimal
    """Participação no total de posições com cotação e gregas calculáveis."""


@dataclass(frozen=True, slots=True)
class OptionPortfolio:
    positions: list[OptionView]
    gain: Decimal
    loss: Decimal
    result: Decimal
    expirations: list[ExpirationTotal]
    moneyness_totals: list[MoneynessTotal]
    total_theta_daily: Decimal | None
    """Soma do decaimento diário esperado do prêmio ("Theta decay
    diário"), já considerando a direção de cada posição — uma opção
    vendida (V) se beneficia da passagem do tempo, então seu theta entra
    com sinal invertido em relação a uma opção comprada (C). ``None`` se
    nenhuma posição tiver gregas calculáveis."""


def build_option_portfolio(
    positions: list[OptionPosition],
    *,
    stale_after_seconds: int,
    risk_free_rate_annual: Decimal,
    today: date | None = None,
    now: datetime | None = None,
) -> OptionPortfolio:
    current_date = today or date.today()
    observed_now = now or datetime.now(UTC)
    views: list[OptionView] = []
    for position in positions:
        quote = position.quote
        if quote is None:
            views.append(OptionView(position, None, "missing", None))
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
        position_greeks = calculate_greeks(
            option_type=position.contract.option_type,
            underlying_price=quote.underlying_price,
            strike=position.contract.strike,
            market_price=quote.last_price,
            remaining_days=position_metrics.remaining_days,
            risk_free_rate_annual=risk_free_rate_annual,
        )
        views.append(OptionView(position, position_metrics, status, position_greeks))

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

    views_with_greeks = [view for view in views if view.greeks is not None]
    moneyness_counts: dict[str, int] = {}
    for view in views_with_greeks:
        assert view.greeks is not None  # narrows for mypy; filtered above
        moneyness_counts[view.greeks.moneyness] = moneyness_counts.get(view.greeks.moneyness, 0) + 1
    total_classified = len(views_with_greeks)
    moneyness_totals = [
        MoneynessTotal(
            moneyness=moneyness,
            count=count,
            pct=Decimal(count) / Decimal(total_classified),
        )
        for moneyness, count in sorted(moneyness_counts.items())
    ]

    def _direction(view: OptionView) -> Decimal:
        return Decimal("1") if view.position.side.value == "C" else Decimal("-1")

    theta_contributions = [
        _direction(view) * view.greeks.theta_daily
        for view in views_with_greeks
        if view.greeks is not None and view.greeks.theta_daily is not None
    ]
    total_theta_daily = (
        sum(theta_contributions, Decimal("0")) if theta_contributions else None
    )

    return OptionPortfolio(
        views, gain, loss, gain + loss, expirations, moneyness_totals, total_theta_daily
    )
