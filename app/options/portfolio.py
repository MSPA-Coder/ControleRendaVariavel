from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.core.instrument_status import instrument_status_class, instrument_status_letter
from app.models import OptionPosition
from app.options.greeks import OptionGreeks, calculate_greeks
from app.options.metrics import OptionMetrics, calculate_option


@dataclass(frozen=True, slots=True)
class OptionView:
    position: OptionPosition
    metrics: OptionMetrics | None
    quote_status: str
    greeks: OptionGreeks | None
    instrument_status: str = ""
    instrument_status_class: str = "unknown"


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
class OptionPortfolioTotal:
    portfolio_name: str
    """Nome da carteira, que titula o card. Os totais sao agrupados por
    (carteira, moeda), como em ``app.positions.portfolio``: cada carteira e um bolso
    separado."""
    simulated: bool
    """``True`` marca o card visualmente. É atributo da carteira, não do
    nome dela: pode haver quantas carteiras simuladas o usuário quiser, e
    cada uma tem o seu card com o seu próprio nome. Dinheiro simulado nunca
    soma no de uma carteira real."""
    currencies: tuple[str, ...]
    """Moeda do card, sempre uma só desde que o agrupamento passou a incluí-la
    na chave. Continua tupla porque o template já a percorre, e o valor vem
    do ticker do contrato (``OptionPosition.currency``), nunca de uma
    constante: uma opção fora do BRL não faz o card anunciar ``R$``."""
    gain: Decimal
    loss: Decimal
    result: Decimal
    total_theta_daily: Decimal | None
    """Soma do decaimento diário esperado do prêmio ("Theta decay
    diário"), já considerando a direção de cada posição — uma opção
    vendida (V) se beneficia da passagem do tempo, então seu theta entra
    com sinal invertido em relação a uma opção comprada (C). ``None`` se
    nenhuma posição deste bucket tiver gregas calculáveis."""


@dataclass(frozen=True, slots=True)
class OptionPortfolio:
    positions: list[OptionView]
    totals: list[OptionPortfolioTotal]
    expirations: list[ExpirationTotal]
    moneyness_totals: list[MoneynessTotal]


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
        letter = instrument_status_letter(quote)
        views.append(
            OptionView(
                position,
                position_metrics,
                status,
                position_greeks,
                instrument_status=letter,
                instrument_status_class=instrument_status_class(letter),
            )
        )

    # "Totais por vencimento" e moneyness excluem a carteira Simulada
    # (decisão do mantenedor): são leituras de exposição/risco por contrato,
    # e dinheiro simulado não pode se ler como exposição real — mesmo
    # mesmo critério de Risco, Performance e exposição, que já excluem
    # incondicionalmente) e do card de totalização por natureza abaixo (ver
    # docstring de ``OptionPortfolioTotal``).
    grouped: dict[date, list[OptionMetrics]] = {}
    for view in views:
        if view.metrics is not None and not view.position.simulated:
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

    views_with_greeks = [
        view for view in views if view.greeks is not None and not view.position.simulated
    ]
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

    # Ganhos, perdas, resultado e theta somam por natureza (real ou
    # simulada), nunca juntos: dinheiro simulado não pode se ler
    # como patrimônio real no card de totalização. O bucket nasce na
    # primeira posição vista daquela natureza, então uma carteira sem
    # nenhuma posição simulada simplesmente não ganha um segundo card (mesmo
    # critério de ``app.positions.portfolio.build_portfolio`` para os totais por
    # moeda).
    # Chave (carteira, moeda), como em ``app.positions.portfolio.build_portfolio``:
    # cada carteira e um bolso separado, e a moeda entra na chave porque
    # somar moedas diferentes sem cambio nao significa nada. A moeda vem do
    # ticker do contrato (``OptionPosition.currency``), nunca de constante.
    Bucket = tuple[int, str]
    metrics_by_bucket: dict[Bucket, list[OptionMetrics]] = {}
    theta_by_bucket: dict[Bucket, list[Decimal]] = {}
    sample_by_bucket: dict[Bucket, OptionPosition] = {}
    for view in views:
        bucket = (view.position.portfolio_id, view.position.currency)
        metrics_by_bucket.setdefault(bucket, [])
        theta_by_bucket.setdefault(bucket, [])
        sample_by_bucket.setdefault(bucket, view.position)
        if view.metrics is not None:
            metrics_by_bucket[bucket].append(view.metrics)
        if view.greeks is not None and view.greeks.theta_daily is not None:
            theta_by_bucket[bucket].append(_direction(view) * view.greeks.theta_daily)

    def _order(bucket: Bucket) -> tuple[bool, str, str]:
        sample = sample_by_bucket[bucket]
        # Reais antes das simuladas, depois por nome e moeda.
        return (sample.simulated, sample.portfolio_ref.name, bucket[1])

    totals = []
    for bucket in sorted(metrics_by_bucket, key=_order):
        sample = sample_by_bucket[bucket]
        bucket_metrics = metrics_by_bucket[bucket]
        gain = sum((metric.result for metric in bucket_metrics if metric.result > 0), Decimal("0"))
        loss = sum((metric.result for metric in bucket_metrics if metric.result < 0), Decimal("0"))
        theta_contributions = theta_by_bucket[bucket]
        total_theta_daily = (
            sum(theta_contributions, Decimal("0")) if theta_contributions else None
        )
        totals.append(
            OptionPortfolioTotal(
                portfolio_name=sample.portfolio_ref.name,
                simulated=sample.simulated,
                currencies=(bucket[1],),
                gain=gain,
                loss=loss,
                result=gain + loss,
                total_theta_daily=total_theta_daily,
            )
        )

    return OptionPortfolio(views, totals, expirations, moneyness_totals)
