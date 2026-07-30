from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

from app.models import Market, Position, Side
from app.portfolio import build_portfolio
from app.routes.helpers import allocation_chart_data, stale_quote_rate


def make_position(
    broker: str,
    currency: str,
    quantity: str,
    price: str,
    status: str = "online",
    ticker: str = "TST",
) -> Position:
    quote = SimpleNamespace(
        last_price=Decimal(price),
        previous_close=Decimal(price),
        source_status=status,
        observed_at=datetime(2026, 7, 27, tzinfo=UTC),
    )
    return cast(
        Position,
        SimpleNamespace(
            broker=broker,
            currency=currency,
            market=Market.B3,
            ticker=ticker,
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


def test_allocation_chart_data_groups_by_currency_and_skips_positions_without_quotes() -> None:
    no_quote = make_position("Ge", "BRL", "1", "10")
    no_quote.quote = None  # type: ignore[attr-defined]
    portfolio = build_portfolio(
        [
            make_position("Ge", "BRL", "1", "10"),
            make_position("Xp", "BRL", "2", "10"),
            make_position("Av", "USD", "3", "20"),
            no_quote,
        ],
        stale_after_seconds=30,
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    charts = allocation_chart_data(portfolio.positions)

    by_currency = {chart["currency"]: chart for chart in charts}
    assert set(by_currency) == {"BRL", "USD"}
    assert len(cast(list, by_currency["BRL"]["labels"])) == 2  # no_quote excluded
    assert len(cast(list, by_currency["USD"]["labels"])) == 1
    weights = [Decimal(w) for w in cast(list, by_currency["BRL"]["weights"])]
    assert sum(weights) == Decimal("1")


def test_allocation_chart_data_empty_when_no_positions() -> None:
    assert allocation_chart_data([]) == []


def test_stale_quote_rate_counts_non_online_positions() -> None:
    portfolio = build_portfolio(
        [
            make_position("Ge", "BRL", "1", "10", status="online"),
            make_position("Xp", "BRL", "1", "10", status="stale"),
            make_position("Av", "BRL", "1", "10", status="online"),
            make_position("Bt", "BRL", "1", "10", status="error"),
        ],
        stale_after_seconds=30,
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    rate = stale_quote_rate(portfolio.positions)

    assert rate == Decimal("2") / Decimal("4")


def test_stale_quote_rate_is_none_for_empty_portfolio() -> None:
    assert stale_quote_rate([]) is None


def test_stale_quote_rate_counts_missing_quotes_as_not_online() -> None:
    position = make_position("Ge", "BRL", "1", "10")
    position.quote = None  # type: ignore[attr-defined]
    portfolio = build_portfolio(
        [position],
        stale_after_seconds=30,
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert stale_quote_rate(portfolio.positions) == Decimal("1")
