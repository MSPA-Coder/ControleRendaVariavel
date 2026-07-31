from __future__ import annotations

from decimal import Decimal

from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import AppSetting, Market, Ticker


def _seed_ticker(symbol: str = "IBOV") -> int:
    ticker = Ticker(
        symbol=symbol,
        trading_name=symbol,
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    db.session.add(ticker)
    db.session.commit()
    return ticker.id


def test_settings_page_shows_default_risk_free_rate(auth_client: FlaskClient) -> None:
    response = auth_client.get("/settings")

    assert response.status_code == 200
    assert b"risk_free_rate_annual" in response.data


def test_settings_updates_risk_free_rate(app: Flask, auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/settings",
        data={
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "0.12",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        assert settings is not None
        assert settings.risk_free_rate_annual == Decimal("0.12")


def test_settings_rejects_risk_free_rate_out_of_range(
    app: Flask, auth_client: FlaskClient
) -> None:
    response = auth_client.post(
        "/settings",
        data={
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "1.5",
        },
    )

    assert response.status_code == 422
    assert b"taxa livre de risco" in response.data.lower()
    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        # Nada foi persistido: continua no valor padrão.
        assert settings is not None
        assert settings.risk_free_rate_annual == Decimal("0.1075")


def test_settings_requires_authentication(client: FlaskClient) -> None:
    response = client.get("/settings")

    assert response.status_code == 302


def test_settings_sets_benchmark_ticker_id(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        ticker_id = _seed_ticker()

    response = auth_client.post(
        "/settings",
        data={
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "0.12",
            "benchmark_ticker_id": str(ticker_id),
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        assert settings is not None
        assert settings.benchmark_ticker_id == ticker_id


def test_settings_clears_benchmark_ticker_id(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        ticker_id = _seed_ticker()
    auth_client.post(
        "/settings",
        data={
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "0.12",
            "benchmark_ticker_id": str(ticker_id),
        },
    )

    auth_client.post(
        "/settings",
        data={
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "0.12",
            "benchmark_ticker_id": "",
        },
    )

    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        assert settings is not None
        assert settings.benchmark_ticker_id is None


def test_settings_rejects_unknown_benchmark_ticker_id(
    app: Flask, auth_client: FlaskClient
) -> None:
    response = auth_client.post(
        "/settings",
        data={
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "0.12",
            "benchmark_ticker_id": "999999",
        },
    )

    assert response.status_code == 422
    assert "referência para o beta".encode() in response.data.lower()
    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        assert settings is not None
        assert settings.benchmark_ticker_id is None
