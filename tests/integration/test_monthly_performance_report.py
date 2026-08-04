from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Broker, Market, Position, PositionKind, QuoteHistory, Side, Ticker

pytestmark = [pytest.mark.critical, pytest.mark.business_rule]


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
    assert "Nenhuma posição aberta encontrada para os filtros." in response.get_data(as_text=True)


def test_monthly_performance_renders_filters_with_current_defaults(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_broker()

    response = auth_client.get("/performance")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<option value="real" selected>Real</option>' in html
    assert '<option value="all" selected>Todas</option>' in html
    assert 'value="stocks"' in html
    assert '>Ações</option>' in html
    assert 'value="week"' in html
    assert 'value="semester"' in html


def test_monthly_performance_filters_by_broker(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        first_ticker_id = _seed_ticker("PETR4")
        second_ticker_id = _seed_ticker("VALE3")
        first_broker_id = _seed_broker()
        second_broker = Broker(name="Rico", acronym="RI")
        db.session.add(second_broker)
        db.session.commit()
        _seed_open_position(first_ticker_id, broker_id=first_broker_id)
        _seed_open_position(second_ticker_id, broker_id=second_broker.id)
        _seed_quote_history(first_ticker_id, [("2026-01-05", "100"), ("2026-02-05", "110")])
        _seed_quote_history(second_ticker_id, [("2026-01-05", "50"), ("2026-02-05", "55")])

    response = auth_client.get("/performance?broker=Genial&portfolio=stocks")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'value="Genial" selected' in html
    assert 'value="stocks" selected' in html
    assert "R$ 1.100,00" in html
    assert "R$ 550,00" not in html


def test_monthly_performance_period_is_selected_and_applied_by_the_backend(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4")
        _seed_open_position(ticker_id, quantity="10")
        _seed_quote_history(
            ticker_id,
            [
                ("2026-07-31", "100"),
                ("2026-08-02", "110"),
                ("2026-08-08", "120"),
            ],
        )

    all_period = auth_client.get("/performance")
    week_period = auth_client.get("/performance?period=week")

    assert "07/2026" in all_period.get_data(as_text=True)
    week_html = week_period.get_data(as_text=True)
    assert week_period.status_code == 200
    assert '<option value="week" selected>Semana</option>' in week_html
    assert "07/2026" not in week_html
    assert "R$ 1.200,00" in week_html


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


@pytest.mark.security
def test_monthly_performance_requires_authentication(client: FlaskClient) -> None:
    assert client.get("/performance").status_code == 302


def test_monthly_performance_defaults_to_stock_portfolio(
    app: Flask, auth_client: FlaskClient
) -> None:
    response = auth_client.get("/performance")

    assert response.status_code == 200
    assert b'<option value="stocks" selected>' in response.data


def test_monthly_performance_offers_and_applies_benchmark_comparison(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4")
        _seed_open_position(ticker_id, quantity="10")
        _seed_quote_history(ticker_id, [("2026-01-31", "100"), ("2026-02-28", "110")])
        benchmark_id = _seed_ticker("BOVA11")
        _seed_quote_history(benchmark_id, [("2026-01-20", "50"), ("2026-02-15", "55")])

    page = auth_client.get(f"/performance?benchmark_ticker_id={benchmark_id}")

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert f'<option value="{benchmark_id}" selected>BOVA11</option>' in html
    assert 'data-benchmark-label="BOVA11"' in html
    assert "Comparando com a evolução percentual de BOVA11" in html
    assert "50" in html and "55" in html  # série mensal alinhada embutida no HTML


def test_monthly_performance_hides_comparison_control_without_candidates(
    auth_client: FlaskClient,
) -> None:
    response = auth_client.get("/performance")

    assert response.status_code == 200
    assert "Comparar com" not in response.get_data(as_text=True)


def test_monthly_performance_ignores_unknown_benchmark_ticker_id(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4")
        _seed_open_position(ticker_id, quantity="10")
        _seed_quote_history(ticker_id, [("2026-01-31", "100")])
        _seed_ticker("BOVA11")  # candidato existe, mas não é o id enviado

    response = auth_client.get("/performance?benchmark_ticker_id=999999")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "data-benchmark-label" not in html
