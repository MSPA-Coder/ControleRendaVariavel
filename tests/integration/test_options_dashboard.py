from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import (
    Broker,
    Market,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionQuote,
    OptionType,
    Side,
    Ticker,
)


def _seed_option_position(risk_free_rate_annual: Decimal | None = None) -> None:
    from app.models import AppSetting

    broker = Broker(name="XP Investimentos", acronym="XP")
    underlying = Ticker(
        symbol="PETR4",
        trading_name="Petrobras",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    option_ticker = Ticker(
        symbol="PETRJ100",
        trading_name="PETR4 call",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    db.session.add_all([broker, underlying, option_ticker])
    db.session.commit()

    expiration = OptionExpiration(call_code="J", put_code="V", exercise_date=date(2027, 7, 27))
    db.session.add(expiration)
    db.session.commit()

    contract = OptionContract(
        ticker_id=option_ticker.id,
        underlying_ticker_id=underlying.id,
        expiration_id=expiration.id,
        option_type=OptionType.CALL,
        strike=Decimal("100"),
    )
    db.session.add(contract)
    db.session.commit()

    position = OptionPosition(
        broker_id=broker.id,
        contract_id=contract.id,
        quantity=Decimal("100"),
        average_cost=Decimal("2"),
        side=Side.BUY,
        opened_on=date(2026, 1, 4),
        result_mode="L",
    )
    db.session.add(position)
    db.session.commit()

    quote = OptionQuote(
        option_position_id=position.id,
        last_price=Decimal("10.4506"),
        previous_close=Decimal("10.4506"),
        underlying_price=Decimal("100"),
        source_status="online",
        observed_at=datetime.now(UTC),
    )
    db.session.add(quote)
    if risk_free_rate_annual is not None:
        settings = db.session.get(AppSetting, 1)
        if settings is None:
            from app.collector_settings import default_collector_settings

            settings = default_collector_settings()
            db.session.add(settings)
        settings.risk_free_rate_annual = risk_free_rate_annual
    db.session.commit()


def test_options_dashboard_renders_greeks_and_charts(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_option_position(risk_free_rate_annual=Decimal("0.05"))

    response = auth_client.get("/options")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for marker in [
        "moneyness-summary",
        "expiration-chart",
        "Theta total",
        "Vol. impl",
        "chart.umd.min.js",
        "expiration-chart.js",
    ]:
        assert marker in html, f"esperava encontrar {marker!r} na página de opções"


def test_options_dashboard_shows_moneyness_badge(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        _seed_option_position(risk_free_rate_annual=Decimal("0.05"))

    response = auth_client.get("/options")

    html = response.get_data(as_text=True)
    assert 'class="status moneyness-ATM"' in html


def test_options_dashboard_requires_authentication(client: FlaskClient) -> None:
    response = client.get("/options")

    assert response.status_code == 302
