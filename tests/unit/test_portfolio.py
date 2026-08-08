from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest

from app.models import Market, Position, Side
from app.portfolio import build_portfolio

pytestmark = [pytest.mark.critical]


def make_position(
    broker: str,
    currency: str,
    quantity: str,
    price: str,
    market: Market = Market.B3,
) -> Position:
    quote = SimpleNamespace(
        last_price=Decimal(price),
        previous_close=Decimal(price),
        source_status="online",
        observed_at=datetime(2026, 7, 27, tzinfo=UTC),
    )
    return cast(
        Position,
        SimpleNamespace(
            broker=broker,
            currency=currency,
            market=market,
            quote=quote,
            side=Side.BUY,
            quantity=Decimal(quantity),
            average_cost=Decimal("8"),
            quote_multiplier=Decimal("1"),
            target_multiplier=Decimal("1.5"),
            opened_on=date(2026, 1, 1),
            result_mode="L",
        ),
    )


def test_portfolio_groups_brokers_and_never_combines_currencies() -> None:
    portfolio = build_portfolio(
        [
            make_position("Ge", "BRL", "1", "10"),
            make_position("Xp", "BRL", "2", "10"),
            make_position("Av", "USD", "3", "20"),
        ],
        stale_after_seconds=30,
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert [(total.currency, total.current_total) for total in portfolio.currency_totals] == [
        ("BRL", Decimal("30")),
        ("USD", Decimal("60")),
    ]
    assert [(group.broker, group.currency) for group in portfolio.broker_groups] == [
        ("Ge", "BRL"),
        ("Xp", "BRL"),
        ("Av", "USD"),
    ]
    assert portfolio.positions[0].current_weight == Decimal("1") / Decimal("3")
    assert portfolio.positions[1].current_weight == Decimal("2") / Decimal("3")
    assert portfolio.positions[2].current_weight == Decimal("1")


def test_currency_totals_expose_return_pct_and_hhi() -> None:
    # Rentabilidade total e índice de concentração da carteira
    # (HHI), sempre calculados por moeda — nunca somando moedas diferentes.
    portfolio = build_portfolio(
        [
            make_position("Ge", "BRL", "1", "10"),
            make_position("Xp", "BRL", "2", "10"),
            make_position("Av", "USD", "3", "20"),
        ],
        stale_after_seconds=30,
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )
    totals_by_currency = {total.currency: total for total in portfolio.currency_totals}

    brl = totals_by_currency["BRL"]
    assert brl.return_pct == brl.result_total / brl.cost_total
    expected_brl_hhi = (Decimal("1") / Decimal("3")) ** 2 + (Decimal("2") / Decimal("3")) ** 2
    assert brl.hhi == expected_brl_hhi

    usd = totals_by_currency["USD"]
    assert usd.return_pct == usd.result_total / usd.cost_total
    assert usd.hhi == Decimal("1")  # uma única posição concentra 100%


def test_currency_totals_return_pct_and_hhi_are_none_without_quotes() -> None:
    position = make_position("Ge", "BRL", "1", "10")
    position.quote = None  # type: ignore[attr-defined]

    portfolio = build_portfolio(
        [position],
        stale_after_seconds=30,
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    total = portfolio.currency_totals[0]
    assert total.return_pct is None
    assert total.hhi is None


def test_broker_groups_expose_weights_within_currency() -> None:
    # Exposição por corretora, com percentual.
    portfolio = build_portfolio(
        [
            make_position("Ge", "BRL", "1", "10"),
            make_position("Xp", "BRL", "2", "10"),
            make_position("Av", "USD", "3", "20"),
        ],
        stale_after_seconds=30,
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )
    by_broker = {(group.broker, group.currency): group for group in portfolio.broker_groups}

    assert by_broker[("Ge", "BRL")].current_weight == Decimal("1") / Decimal("3")
    assert by_broker[("Xp", "BRL")].current_weight == Decimal("2") / Decimal("3")
    assert by_broker[("Av", "USD")].current_weight == Decimal("1")
    # Weights within a currency always add up to 1 (never mixed across currencies).
    brl_weight_sum = sum(
        group.current_weight for group in portfolio.broker_groups if group.currency == "BRL"
    )
    assert brl_weight_sum == Decimal("1")


def test_market_groups_aggregate_across_brokers_within_same_market_and_currency() -> None:
    # Exposição por mercado (B3/NYSE/NASDAQ).
    portfolio = build_portfolio(
        [
            make_position("Ge", "BRL", "1", "10", market=Market.B3),
            make_position("Xp", "BRL", "2", "10", market=Market.B3),
            make_position("Bt", "BRL", "1", "10", market=Market.NASDAQ),
            make_position("Av", "USD", "3", "20", market=Market.NYSE),
        ],
        stale_after_seconds=30,
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )
    by_market = {(group.market, group.currency): group for group in portfolio.market_groups}

    # Ge and Xp are different brokers but the same market: they merge into
    # a single B3/BRL group, unlike broker_groups which keeps them separate.
    b3_brl = by_market[(Market.B3, "BRL")]
    assert b3_brl.current_total == Decimal("30")
    assert b3_brl.current_weight == Decimal("30") / Decimal("40")
    assert {view.position.broker for view in b3_brl.positions} == {"Ge", "Xp"}

    nasdaq_brl = by_market[(Market.NASDAQ, "BRL")]
    assert nasdaq_brl.current_total == Decimal("10")
    assert nasdaq_brl.current_weight == Decimal("10") / Decimal("40")

    nyse_usd = by_market[(Market.NYSE, "USD")]
    assert nyse_usd.current_weight == Decimal("1")
