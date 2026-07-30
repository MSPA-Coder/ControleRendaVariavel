from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Broker, Market, Position, PositionKind, Quote, Side, Ticker


def _seed_two_positions_one_stale() -> None:
    broker = Broker(name="XP", acronym="XP")
    t1 = Ticker(
        symbol="PETR4",
        trading_name="Petrobras",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    t2 = Ticker(
        symbol="VALE3",
        trading_name="Vale",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    db.session.add_all([broker, t1, t2])
    db.session.commit()
    p1 = Position(
        broker_id=broker.id,
        ticker_id=t1.id,
        quantity=Decimal("100"),
        average_cost=Decimal("20"),
        side=Side.BUY,
        opened_on=date(2026, 1, 1),
        quote_multiplier=Decimal("1"),
        target_multiplier=Decimal("1.5"),
        result_mode="L",
        position_kind=PositionKind.REAL,
    )
    p2 = Position(
        broker_id=broker.id,
        ticker_id=t2.id,
        quantity=Decimal("50"),
        average_cost=Decimal("40"),
        side=Side.BUY,
        opened_on=date(2026, 1, 1),
        quote_multiplier=Decimal("1"),
        target_multiplier=Decimal("1.5"),
        result_mode="L",
        position_kind=PositionKind.REAL,
    )
    db.session.add_all([p1, p2])
    db.session.commit()
    db.session.add(
        Quote(
            position_id=p1.id,
            last_price=Decimal("25"),
            previous_close=Decimal("25"),
            source_status="online",
            observed_at=datetime.now(UTC),
        )
    )
    db.session.add(
        Quote(
            position_id=p2.id,
            last_price=Decimal("35"),
            previous_close=Decimal("35"),
            source_status="stale",
            observed_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    )
    db.session.commit()


def test_dashboard_renders_allocation_chart_and_stale_rate(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_two_positions_one_stale()

    response = auth_client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "allocation-chart" in html
    assert "Cotações desatualizadas" in html
    assert "50,0%" in html  # 1 de 2 posições está stale
    assert "chart.umd.min.js" in html
    assert "allocation-chart.js" in html
