from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest

from app.models import Market, Position, Side
from app.portfolio import build_portfolio
from app.routes.helpers import (
    allocation_chart_data,
    broker_exposure_chart_data,
    market_exposure_chart_data,
)

pytestmark = [pytest.mark.business_rule]


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


def _mixed_currency_portfolio():  # type: ignore[no-untyped-def]
    return build_portfolio(
        [
            make_position("Ge", "BRL", "1", "10"),
            make_position("Xp", "BRL", "2", "10"),
            make_position("Av", "USD", "3", "20"),
        ],
        stale_after_seconds=30,
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )


def test_broker_exposure_chart_data_groups_by_currency_and_labels_brokers() -> None:
    charts = broker_exposure_chart_data(_mixed_currency_portfolio().broker_groups)

    by_currency = {chart["currency"]: chart for chart in charts}
    assert set(by_currency) == {"BRL", "USD"}
    assert set(cast(list, by_currency["BRL"]["labels"])) == {"Ge", "Xp"}
    assert sum(Decimal(w) for w in cast(list, by_currency["BRL"]["weights"])) == Decimal("1")


def test_market_exposure_chart_data_labels_markets_by_value() -> None:
    charts = market_exposure_chart_data(_mixed_currency_portfolio().market_groups)

    by_currency = {chart["currency"]: chart for chart in charts}
    # make_position usa Market.B3 para todas, então cada moeda tem um grupo
    # de mercado que concentra 100% dela.
    assert cast(list, by_currency["BRL"]["labels"]) == ["B3"]
    assert cast(list, by_currency["USD"]["labels"]) == ["B3"]
    assert Decimal(cast(list, by_currency["USD"]["weights"])[0]) == Decimal("1")


def test_exposure_chart_data_is_empty_without_groups() -> None:
    assert broker_exposure_chart_data([]) == []
    assert market_exposure_chart_data([]) == []
