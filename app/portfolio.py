from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain import PositionMetrics, calculate_position, safe_div
from app.instrument_status import instrument_status_class, instrument_status_letter
from app.models import Market, Position


@dataclass(frozen=True, slots=True)
class PositionView:
    position: Position
    metrics: PositionMetrics | None
    current_weight: Decimal | None
    cost_weight: Decimal | None
    quote_status: str
    instrument_status: str
    instrument_status_class: str


@dataclass(frozen=True, slots=True)
class PortfolioTotal:
    currency: str
    portfolio_name: str
    """Nome da carteira, que é o título do card.

    Os totais são agrupados por (carteira, moeda), não por moeda: cada
    carteira é um bolso separado, e somar duas delas produziria um número
    que ninguém pediu. A moeda entra na chave porque somar BRL e USD sem
    taxa de câmbio não significa nada — uma carteira com as duas rende dois
    cards."""
    simulated: bool
    """``True`` marca o card visualmente. É atributo da carteira, não do
    nome dela: pode haver quantas carteiras simuladas o usuário quiser, e
    cada uma tem o seu card com o seu próprio nome."""
    current_total: Decimal
    cost_total: Decimal
    result_total: Decimal
    return_pct: Decimal | None
    """Σ resultado / Σ custo desta moeda (e desta natureza, real ou
    simulada).

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
    simulated: bool
    positions: list[PositionView]
    current_total: Decimal
    cost_total: Decimal
    result_total: Decimal
    current_weight: Decimal | None
    """Participação desta corretora no valor atual total da mesma moeda e
    natureza (real ou simulada — ver ``PortfolioTotal.simulated``)."""
    cost_weight: Decimal | None
    """Participação desta corretora no custo total da mesma moeda e
    natureza."""


@dataclass(frozen=True, slots=True)
class MarketGroup:
    market: Market
    currency: str
    simulated: bool
    positions: list[PositionView]
    current_total: Decimal
    cost_total: Decimal
    result_total: Decimal
    current_weight: Decimal | None
    """Participação deste mercado (B3/NYSE/NASDAQ) no valor atual total da
    mesma moeda e natureza (real ou simulada)."""
    cost_weight: Decimal | None
    """Participação deste mercado no custo total da mesma moeda e
    natureza."""


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
    return_period_days: int = 365,
    today: date | None = None,
    now: datetime | None = None,
) -> PortfolioView:
    observed_now = now or datetime.now(UTC)
    calculated: list[tuple[Position, PositionMetrics | None, str]] = []
    # Chave (carteira, moeda): cada carteira e um bolso separado, e uma
    # posicao de uma nao deve inflar o total, o peso (%) nem o HHI de outra
    # -- mesmo com a mesma moeda, e mesmo quando as duas sao reais. A moeda
    # entra na chave porque somar BRL e USD sem cambio nao significa nada.
    totals_by_bucket: dict[tuple[int, str], list[Decimal]] = {}

    def _bucket_of(position: Position) -> tuple[int, str]:
        return (position.portfolio_id, position.currency)

    for position in positions:
        bucket = _bucket_of(position)
        bucket_totals = totals_by_bucket.setdefault(
            bucket, [Decimal("0"), Decimal("0"), Decimal("0")]
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
            return_period_days=return_period_days,
            today=today,
        )
        bucket_totals[0] += abs(metrics.unwind_value)
        bucket_totals[1] += abs(metrics.build_value)
        bucket_totals[2] += metrics.result
        calculated.append((position, metrics, status))

    views = [
        PositionView(
            position=position,
            metrics=metrics,
            current_weight=(
                abs(metrics.unwind_value) / totals_by_bucket[_bucket_of(position)][0]
                if metrics is not None and totals_by_bucket[_bucket_of(position)][0]
                else None
            ),
            cost_weight=(
                abs(metrics.build_value) / totals_by_bucket[_bucket_of(position)][1]
                if metrics is not None and totals_by_bucket[_bucket_of(position)][1]
                else None
            ),
            quote_status=status,
            instrument_status=instrument_status_letter(position.quote),
            instrument_status_class=instrument_status_class(
                instrument_status_letter(position.quote)
            ),
        )
        for position, metrics, status in calculated
    ]
    # Os grupos de corretora e mercado dividem o mesmo bucket dos totais
    # (carteira, moeda): o peso de uma corretora e a fatia dela dentro
    # daquela carteira e moeda, nao de um total misturado.
    grouped: dict[tuple[str, int, str], list[PositionView]] = {}
    market_grouped: dict[tuple[Market, int, str], list[PositionView]] = {}
    for view in views:
        portfolio_id, currency = _bucket_of(view.position)
        grouped.setdefault((view.position.broker, portfolio_id, currency), []).append(view)
        market_grouped.setdefault(
            (view.position.market, portfolio_id, currency), []
        ).append(view)

    def _aggregate(
        group_views: list[PositionView], bucket: tuple[int, str]
    ) -> tuple[Decimal, Decimal, Decimal, Decimal | None, Decimal | None]:
        group_metrics = [view.metrics for view in group_views if view.metrics is not None]
        current_total = sum((abs(metric.unwind_value) for metric in group_metrics), Decimal("0"))
        cost_total = sum((abs(metric.build_value) for metric in group_metrics), Decimal("0"))
        result_total = sum((metric.result for metric in group_metrics), Decimal("0"))
        bucket_current_total, bucket_cost_total, _ = totals_by_bucket[bucket]
        return (
            current_total,
            cost_total,
            result_total,
            safe_div(current_total, bucket_current_total),
            safe_div(cost_total, bucket_cost_total),
        )

    broker_groups = [
        BrokerGroup(
            broker,
            currency,
            group_views[0].position.simulated,
            group_views,
            *_aggregate(group_views, (portfolio_id, currency)),
        )
        for (broker, portfolio_id, currency), group_views in grouped.items()
    ]
    market_groups = [
        MarketGroup(
            market,
            currency,
            group_views[0].position.simulated,
            group_views,
            *_aggregate(group_views, (portfolio_id, currency)),
        )
        for (market, portfolio_id, currency), group_views in market_grouped.items()
    ]

    # Carteiras reais antes das simuladas, e dentro de cada grupo por nome e
    # moeda: a ordem de insercao de `totals_by_bucket` segue a dos
    # `positions` recebidos, que nao tem relacao com "real antes de
    # simulada". Um pedido ordenado e o que garante os cards reais sempre
    # aparecerem primeiro, e nao intercalados com os simulados.
    positions_by_bucket: dict[tuple[int, str], Position] = {}
    for position in positions:
        positions_by_bucket.setdefault(_bucket_of(position), position)

    def _order(bucket: tuple[int, str]) -> tuple[bool, str, str]:
        sample = positions_by_bucket[bucket]
        return (sample.simulated, sample.portfolio_ref.name, bucket[1])

    currency_total_views = []
    for bucket, (current_total, cost_total, result_total) in sorted(
        totals_by_bucket.items(), key=lambda item: _order(item[0])
    ):
        sample = positions_by_bucket[bucket]
        weights = [
            view.current_weight
            for view in views
            if _bucket_of(view.position) == bucket and view.current_weight is not None
        ]
        hhi = sum((weight * weight for weight in weights), Decimal("0")) if weights else None
        currency_total_views.append(
            PortfolioTotal(
                currency=bucket[1],
                portfolio_name=sample.portfolio_ref.name,
                simulated=sample.simulated,
                current_total=current_total,
                cost_total=cost_total,
                result_total=result_total,
                return_pct=safe_div(result_total, cost_total),
                hhi=hhi,
            )
        )
    return PortfolioView(views, currency_total_views, broker_groups, market_groups)
