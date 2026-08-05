from __future__ import annotations

from datetime import date

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import (
    Broker,
    Market,
    OptionExpiration,
    Position,
    PositionKind,
    Side,
    Ticker,
)

pytestmark = [pytest.mark.critical, pytest.mark.business_rule]


def _seed_broker() -> int:
    broker = Broker(name="XP Investimentos", acronym="XP")
    db.session.add(broker)
    db.session.commit()
    return broker.id


def _seed_ticker(symbol: str, *, is_benchmark: bool = False) -> int:
    ticker = Ticker(
        symbol=symbol,
        trading_name=symbol,
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
        is_benchmark=is_benchmark,
    )
    db.session.add(ticker)
    db.session.commit()
    return ticker.id


# --- Cadastro (Tabelas > Tickers) -------------------------------------------


def test_ticker_create_persists_is_benchmark_checkbox(app: Flask, auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/tables/tickers",
        data={
            "symbol": "BOVA11",
            "trading_name": "iShares Bovespa",
            "market": "B3",
            "rtd_market_code": "B",
            "currency": "BRL",
            "is_benchmark": "1",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        ticker = db.session.scalar(db.select(Ticker).where(Ticker.symbol == "BOVA11"))
        assert ticker is not None
        assert ticker.is_benchmark is True

    html = response.get_data(as_text=True)
    checkbox_start = html.find('aria-label="BOVA11 é referência de comparação"')
    assert checkbox_start != -1
    assert "checked" in html[checkbox_start : checkbox_start + 100]


def test_ticker_create_without_checkbox_is_not_a_benchmark(
    app: Flask, auth_client: FlaskClient
) -> None:
    response = auth_client.post(
        "/tables/tickers",
        data={
            "symbol": "PETR4",
            "trading_name": "Petrobras",
            "market": "B3",
            "rtd_market_code": "B",
            "currency": "BRL",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        ticker = db.session.scalar(db.select(Ticker).where(Ticker.symbol == "PETR4"))
        assert ticker is not None
        assert ticker.is_benchmark is False


def test_ticker_cannot_become_a_benchmark_while_it_has_an_open_position(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        broker_id = _seed_broker()
        ticker_id = _seed_ticker("PETR4")
        db.session.add(
            Position(
                broker_id=broker_id,
                ticker_id=ticker_id,
                quantity=10,
                average_cost=20,
                side=Side.BUY,
                opened_on=date(2026, 1, 1),
                quote_multiplier=1,
                target_multiplier=1.5,
                result_mode="L",
                position_kind=PositionKind.REAL,
            )
        )
        db.session.commit()

    response = auth_client.post(
        f"/tables/tickers/{ticker_id}",
        data={
            "symbol": "PETR4",
            "trading_name": "Petrobras",
            "market": "B3",
            "rtd_market_code": "B",
            "currency": "BRL",
            "is_benchmark": "1",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "não pode virar referência de comparação" in response.get_data(as_text=True)
    with app.app_context():
        ticker = db.session.get(Ticker, ticker_id)
        assert ticker is not None
        assert ticker.is_benchmark is False


# --- Exclusão dos formulários de ativos -------------------------------------


def test_benchmark_ticker_is_absent_from_the_position_form_dropdown(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_ticker("PETR4")
        _seed_ticker("BOVA11", is_benchmark=True)

    html = auth_client.get("/positions/new").get_data(as_text=True)

    assert "PETR4 · B3 · BRL" in html
    assert "BOVA11" not in html


def test_position_rejects_a_benchmark_ticker_id_submitted_directly(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        broker_id = _seed_broker()
        benchmark_id = _seed_ticker("BOVA11", is_benchmark=True)

    response = auth_client.post(
        "/positions",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(benchmark_id),
            "quantity": "100",
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
    assert "referência de comparação" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.scalar(db.select(Position)) is None


def test_dividend_rejects_a_benchmark_ticker_id_submitted_directly(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        broker_id = _seed_broker()
        benchmark_id = _seed_ticker("BOVA11", is_benchmark=True)

    response = auth_client.post(
        "/dividends",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(benchmark_id),
            "amount": "12.50",
            "payment_date": "2026-02-15",
        },
    )

    assert response.status_code == 422
    with app.app_context():
        from app.models import Dividend

        assert db.session.scalar(db.select(Dividend)) is None


def test_transaction_rejects_a_benchmark_ticker_id_submitted_directly(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        broker_id = _seed_broker()
        benchmark_id = _seed_ticker("BOVA11", is_benchmark=True)

    response = auth_client.post(
        "/transactions",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(benchmark_id),
            "quantity": "100",
            "average_cost": "20",
            "exit_price": "25",
            "side": "C",
            "opened_on": "2026-01-01",
            "closed_on": "2026-03-01",
            "result_mode": "L",
            "position_kind": "real",
        },
    )

    assert response.status_code == 422
    with app.app_context():
        from app.models import Transaction

        assert db.session.scalar(db.select(Transaction)) is None


def test_option_contract_rejects_a_benchmark_ticker_as_underlying(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        option_ticker_id = _seed_ticker("PETRJ100")
        benchmark_id = _seed_ticker("BOVA11", is_benchmark=True)
        expiration = OptionExpiration(
            call_code="27J01", put_code="27V01", exercise_date=date(2027, 7, 27)
        )
        db.session.add(expiration)
        db.session.commit()
        expiration_id = expiration.id

    response = auth_client.post(
        "/tables/options/contracts",
        data={
            "ticker_id": str(option_ticker_id),
            "underlying_ticker_id": str(benchmark_id),
            "expiration_id": str(expiration_id),
            "option_type": "CALL",
            "strike": "30",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        from app.models import OptionContract

        assert db.session.scalar(db.select(OptionContract)) is None


# --- Comparadores de Cotações e Performance ---------------------------------


def test_benchmark_candidates_are_offered_after_being_registered_as_such(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4")
        benchmark_id = _seed_ticker("BOVA11", is_benchmark=True)

    page = auth_client.get(f"/quotes?ticker_id={ticker_id}")

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert f'value="{benchmark_id}"' in html
    assert ">BOVA11</option>" in html


def test_monthly_performance_hides_comparison_for_options_portfolio(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_ticker("BOVA11", is_benchmark=True)

    response = auth_client.get("/performance?portfolio=options")

    assert response.status_code == 200
    assert "Comparar com" not in response.get_data(as_text=True)


def test_monthly_performance_hides_comparison_for_the_combined_portfolio(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_ticker("BOVA11", is_benchmark=True)

    response = auth_client.get("/performance?portfolio=all")

    assert response.status_code == 200
    assert "Comparar com" not in response.get_data(as_text=True)
