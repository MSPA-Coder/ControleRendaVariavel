from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Broker, Market, Position, PositionKind, QuoteHistory, Side, Ticker


def _seed_ticker(symbol: str, currency: str = "BRL") -> int:
    ticker = Ticker(
        symbol=symbol,
        trading_name=symbol,
        market=Market.B3,
        rtd_market_code="B",
        currency=currency,
    )
    db.session.add(ticker)
    db.session.commit()
    return ticker.id


def _seed_broker() -> int:
    broker = Broker(name="Genial", acronym="GE")
    db.session.add(broker)
    db.session.commit()
    return broker.id


def _seed_open_position(ticker_id: int, quantity: str = "10", broker_id: int | None = None) -> None:
    db.session.add(
        Position(
            broker_id=broker_id or _seed_broker(),
            ticker_id=ticker_id,
            quantity=Decimal(quantity),
            average_cost=Decimal("10"),
            side=Side.BUY,
            opened_on=date(2026, 1, 1),
            quote_multiplier=Decimal("1"),
            target_multiplier=Decimal("1.5"),
            result_mode="L",
            position_kind=PositionKind.REAL,
        )
    )
    db.session.commit()


def _seed_quote_history(ticker_id: int, prices: list[tuple[str, str]]) -> None:
    for day, price in prices:
        recorded_date = date.fromisoformat(day)
        db.session.add(
            QuoteHistory(
                ticker_id=ticker_id,
                price=Decimal(price),
                recorded_date=recorded_date,
                recorded_at=datetime.combine(recorded_date, datetime.min.time(), tzinfo=UTC),
            )
        )
    db.session.commit()


def test_monthly_performance_shows_evolution_and_monthly_return(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4")
        _seed_open_position(ticker_id, quantity="10")
        _seed_quote_history(
            ticker_id,
            [
                ("2026-01-05", "100"),
                ("2026-01-31", "110"),
                ("2026-02-10", "105"),
                ("2026-02-28", "120"),
            ],
        )

    response = auth_client.get("/performance")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "01/2026" in html
    assert "02/2026" in html


def test_monthly_performance_shows_placeholder_without_open_positions(
    auth_client: FlaskClient,
) -> None:
    response = auth_client.get("/performance")

    assert response.status_code == 200
    assert "Nenhuma posição real aberta" in response.get_data(as_text=True)


def test_monthly_performance_groups_by_currency(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        brl_ticker_id = _seed_ticker("PETR4", currency="BRL")
        usd_ticker_id = _seed_ticker("AAPL", currency="USD")
        broker_id = _seed_broker()
        _seed_open_position(brl_ticker_id, quantity="10", broker_id=broker_id)
        _seed_open_position(usd_ticker_id, quantity="5", broker_id=broker_id)
        _seed_quote_history(brl_ticker_id, [("2026-01-05", "100"), ("2026-02-05", "110")])
        _seed_quote_history(usd_ticker_id, [("2026-01-05", "50"), ("2026-02-05", "55")])

    response = auth_client.get("/performance")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "BRL" in html
    assert "USD" in html


def test_monthly_performance_requires_authentication(client: FlaskClient) -> None:
    assert client.get("/performance").status_code == 302
