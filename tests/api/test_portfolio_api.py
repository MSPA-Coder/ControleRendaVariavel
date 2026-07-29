from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Broker, Market, Position, PositionKind, Side, Ticker


def _seed_positions(count: int) -> None:
    broker = Broker(name="Corretora Teste", acronym="CT")
    db.session.add(broker)
    db.session.flush()
    for index in range(count):
        ticker = Ticker(
            symbol=f"TST{index}",
            trading_name=f"Teste {index}",
            market=Market.B3,
            rtd_market_code="B",
            currency="BRL",
        )
        db.session.add(ticker)
        db.session.flush()
        db.session.add(
            Position(
                broker_id=broker.id,
                ticker_id=ticker.id,
                quantity=Decimal("100"),
                average_cost=Decimal("10"),
                side=Side.BUY,
                opened_on=date(2026, 1, index + 1),
                quote_multiplier=Decimal("1"),
                target_multiplier=Decimal("1.5"),
                result_mode="L",
                position_kind=PositionKind.REAL,
            )
        )
    db.session.commit()


def test_portfolio_api_requires_authentication(client: FlaskClient) -> None:
    response = client.get("/api/portfolio")

    assert response.status_code == 401


def test_portfolio_api_paginates_rows_without_breaking_totals(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_positions(5)

    first_page = auth_client.get("/api/portfolio?per_page=2&page=1").get_json()
    second_page = auth_client.get("/api/portfolio?per_page=2&page=2").get_json()

    assert len(first_page["rows"]) == 2
    assert len(second_page["rows"]) == 2
    assert first_page["pagination"] == {
        "page": 1,
        "per_page": 2,
        "total": 5,
        "total_pages": 3,
    }
    # Rows differ between pages...
    assert {row["id"] for row in first_page["rows"]}.isdisjoint(
        {row["id"] for row in second_page["rows"]}
    )
    # ...but totals are aggregated over the whole portfolio, not just the page.
    assert first_page["totals"] == second_page["totals"]


def test_portfolio_api_caches_short_lived_responses(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_positions(1)

    first = auth_client.get("/api/portfolio").get_json()
    with app.app_context():
        # Mutate the DB directly; a cached response should not see this yet.
        db.session.execute(db.delete(Position))
        db.session.commit()
    second = auth_client.get("/api/portfolio").get_json()

    assert first["pagination"]["total"] == 1
    assert second["pagination"]["total"] == 1  # served from cache, not re-queried
