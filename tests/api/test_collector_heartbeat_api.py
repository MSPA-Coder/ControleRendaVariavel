from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from flask.testing import FlaskClient

from app import db
from app.models import Broker, Market, Position, Quote, Side, Ticker

pytestmark = [pytest.mark.critical, pytest.mark.observable_contract]


@pytest.mark.security
def test_collector_heartbeat_requires_authentication(client: FlaskClient) -> None:
    assert client.get("/api/collector-heartbeat").status_code == 401


def test_collector_heartbeat_reports_latest_snapshot(auth_client: FlaskClient) -> None:
    broker = Broker(name="Corretora", acronym="CT")
    ticker = Ticker(
        symbol="TEST3", trading_name="Teste", market=Market.B3,
        rtd_market_code="B", currency="BRL",
    )
    db.session.add_all([broker, ticker])
    db.session.flush()
    position = Position(
        broker_id=broker.id, ticker_id=ticker.id, quantity=Decimal("1"),
        average_cost=Decimal("1"), side=Side.BUY, opened_on=datetime(2026, 1, 1).date(),
        quote_multiplier=Decimal("1"), target_multiplier=Decimal("1.5"), result_mode="L",
    )
    db.session.add(position)
    db.session.flush()
    observed_at = datetime.now(UTC)
    db.session.add(Quote(
        position_id=position.id, last_price=Decimal("1"), previous_close=Decimal("1"),
        instrument_status="", source_status="online", observed_at=observed_at,
    ))
    db.session.commit()

    payload = auth_client.get("/api/collector-heartbeat").get_json()

    assert payload["status"] == "online"
    assert payload["last_read_at"] == observed_at.isoformat()
