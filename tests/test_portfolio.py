from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

from app.models import Position, Side
from app.portfolio import build_portfolio


def make_position(
    broker: str,
    currency: str,
    quantity: str,
    price: str,
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
