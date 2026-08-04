from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import (
    Broker,
    Market,
    OptionExpiration,
    OptionType,
    Ticker,
)

pytestmark = [pytest.mark.critical, pytest.mark.business_rule]


def _seed_reference_data() -> tuple[int, int]:
    broker = Broker(name="XP Investimentos", acronym="XP")
    ticker = Ticker(
        symbol="PETR4",
        trading_name="Petrobras",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    db.session.add_all([broker, ticker])
    db.session.commit()
    return broker.id, ticker.id


def _seed_option_reference_data() -> tuple[int, int, int, int]:
    broker_id, underlying_ticker_id = _seed_reference_data()
    option_ticker = Ticker(
        symbol="PETRA1",
        trading_name="PETRA1",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    expiration = db.session.scalar(
        db.select(OptionExpiration).where(OptionExpiration.call_code == "2026A")
    )
    db.session.add(option_ticker)
    db.session.commit()
    assert expiration is not None
    return broker_id, underlying_ticker_id, option_ticker.id, expiration.id


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_positions_reject_non_finite_quantity(
    app: Flask, auth_client: FlaskClient, value: str
) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_reference_data()

    response = auth_client.post(
        "/positions",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(ticker_id),
            "quantity": value,
            "average_cost": "30.50",
            "side": "C",
            "opened_on": "2026-01-15",
            "quote_multiplier": "1",
            "target_multiplier": "1.5",
            "result_mode": "L",
            "position_kind": "real",
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_transactions_reject_non_finite_exit_price(
    app: Flask, auth_client: FlaskClient, value: str
) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_reference_data()

    response = auth_client.post(
        "/transactions",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(ticker_id),
            "quantity": "100",
            "average_cost": "20",
            "exit_price": value,
            "side": "C",
            "opened_on": "2026-01-01",
            "closed_on": "2026-03-01",
            "result_mode": "L",
            "position_kind": "real",
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_dividends_reject_non_finite_amount(
    app: Flask, auth_client: FlaskClient, value: str
) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_reference_data()

    response = auth_client.post(
        "/dividends",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(ticker_id),
            "amount": value,
            "payment_date": "2026-02-15",
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_quotes_reject_non_finite_manual_price(
    app: Flask, auth_client: FlaskClient, value: str
) -> None:
    with app.app_context():
        _, ticker_id = _seed_reference_data()

    response = auth_client.post(
        "/quotes",
        data={"ticker_id": str(ticker_id), "recorded_date": "2026-01-05", "price": value},
    )

    assert response.status_code == 302
    with app.app_context():
        from app.models import QuoteHistory

        assert db.session.query(QuoteHistory).count() == 0


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_settings_reject_non_finite_risk_free_rate(
    auth_client: FlaskClient, value: str
) -> None:
    response = auth_client.post(
        "/settings",
        data={
            "operational_profile": "test",
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": value,
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_option_contracts_reject_non_finite_strike(
    app: Flask, auth_client: FlaskClient, value: str
) -> None:
    with app.app_context():
        _, underlying_ticker_id, option_ticker_id, expiration_id = _seed_option_reference_data()

    response = auth_client.post(
        "/tables/options/contracts",
        data={
            "ticker_id": str(option_ticker_id),
            "underlying_ticker_id": str(underlying_ticker_id),
            "expiration_id": str(expiration_id),
            "option_type": OptionType.CALL.value,
            "strike": value,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Contrato inválido".encode() in response.data
