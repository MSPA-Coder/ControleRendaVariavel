from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Broker, Market, Position, Ticker

pytestmark = [pytest.mark.critical]


def _seed_reference_data() -> tuple[int, int]:
    broker = Broker(name="XP Investimentos", acronym="XP")
    ticker = Ticker(
        symbol="PETR4",
        trading_name="Petrobras PN",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    db.session.add_all([broker, ticker])
    db.session.commit()
    return broker.id, ticker.id


def test_position_full_crud_roundtrip(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_reference_data()

    create_response = auth_client.post(
        "/positions",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(ticker_id),
            "quantity": "100",
            "average_cost": "30.50",
            "side": "C",
            "opened_on": "2026-01-15",
            "quote_multiplier": "1",
            "target_multiplier": "1.5",
            "result_mode": "L",
            "position_kind": "real",
        },
        follow_redirects=True,
    )
    assert create_response.status_code == 200

    with app.app_context():
        position = db.session.scalar(db.select(Position))
        assert position is not None
        assert position.quantity == 100
        position_id = position.id

    update_response = auth_client.post(
        f"/positions/{position_id}",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(ticker_id),
            "quantity": "200",
            "average_cost": "31.00",
            "side": "C",
            "opened_on": "2026-01-15",
            "quote_multiplier": "1",
            "target_multiplier": "1.5",
            "result_mode": "L",
            "position_kind": "real",
        },
        follow_redirects=True,
    )
    assert update_response.status_code == 200

    with app.app_context():
        position = db.session.get(Position, position_id)
        assert position is not None
        assert position.quantity == 200

    delete_response = auth_client.post(
        f"/positions/{position_id}/delete", follow_redirects=True
    )
    assert delete_response.status_code == 200

    with app.app_context():
        assert db.session.get(Position, position_id) is None


@pytest.mark.security
def test_positions_routes_require_authentication(client: FlaskClient) -> None:
    response = client.get("/positions/new")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
