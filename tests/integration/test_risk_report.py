from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import AppSetting, Market, Position, PositionKind, QuoteHistory, Side, Ticker

pytestmark = [pytest.mark.critical]


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


def _seed_open_position(ticker_id: int, quantity: str = "100") -> None:
    db.session.add(
        Position(
            broker_id=_seed_broker(),
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


def _seed_broker() -> int:
    from app.models import Broker

    broker = Broker(name="Genial", acronym="GE")
    db.session.add(broker)
    db.session.commit()
    return broker.id


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


def test_risk_report_shows_metrics_for_open_position_with_history(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4")
        _seed_open_position(ticker_id)
        _seed_quote_history(
            ticker_id,
            [
                ("2026-01-01", "30"),
                ("2026-01-02", "31"),
                ("2026-01-03", "29"),
                ("2026-01-04", "32"),
            ],
        )

    response = auth_client.get("/risk")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "PETR4" in html


def test_risk_report_shows_placeholder_without_open_positions(
    auth_client: FlaskClient,
) -> None:
    response = auth_client.get("/risk")

    assert response.status_code == 200
    assert "Nenhuma posição real aberta" in response.get_data(as_text=True)


def test_risk_report_computes_beta_against_configured_benchmark(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        asset_id = _seed_ticker("PETR4")
        benchmark_id = _seed_ticker("IBOV")
        _seed_open_position(asset_id)
        prices = [
            ("2026-01-01", "30"),
            ("2026-01-02", "31"),
            ("2026-01-03", "29"),
            ("2026-01-04", "32"),
            ("2026-01-05", "33"),
        ]
        _seed_quote_history(asset_id, prices)
        _seed_quote_history(benchmark_id, prices)
        settings = db.session.get(AppSetting, 1)
        if settings is None:
            settings = AppSetting(id=1)
            db.session.add(settings)
        settings.benchmark_ticker_id = benchmark_id
        db.session.commit()

    response = auth_client.get("/risk")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "IBOV" in html
    # Mesma série para ativo e benchmark -> beta = 1,00.
    assert "1,00" in html


def test_risk_report_shows_portfolio_drawdown_by_currency(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4", currency="BRL")
        _seed_open_position(ticker_id, quantity="10")
        _seed_quote_history(
            ticker_id,
            [("2026-01-01", "100"), ("2026-01-02", "80")],
        )

    response = auth_client.get("/risk")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Máx. drawdown da carteira" in html
    assert "BRL" in html


@pytest.mark.security
def test_risk_report_requires_authentication(client: FlaskClient) -> None:
    assert client.get("/risk").status_code == 302
