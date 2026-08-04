from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Market, QuoteHistory, Ticker

pytestmark = [pytest.mark.business_rule, pytest.mark.observable_contract]


def _seed_ticker() -> int:
    ticker = Ticker(
        symbol="IBOV",
        trading_name="Ibovespa",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    db.session.add(ticker)
    db.session.commit()
    return ticker.id


def test_create_quote_history_entry_then_shows_in_chart_data(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker()

    response = auth_client.post(
        "/quotes",
        data={"ticker_id": str(ticker_id), "recorded_date": "2026-01-05", "price": "125000"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        rows = db.session.query(QuoteHistory).filter_by(ticker_id=ticker_id).all()
        assert len(rows) == 1
        assert rows[0].price == Decimal("125000.00000000")

    page = auth_client.get(f"/quotes?ticker_id={ticker_id}")
    assert page.status_code == 200
    assert b"2026-01-05" in page.data
    html = page.get_data(as_text=True)
    assert "Gerenciar Cotações" in html
    assert "Histórico de IBOV" not in html
    assert "Atualizar Cotações Diárias" in html
    assert "Excluir Cotação" in html


def test_create_quote_history_entry_upserts_same_day(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        ticker_id = _seed_ticker()

    auth_client.post(
        "/quotes",
        data={"ticker_id": str(ticker_id), "recorded_date": "2026-01-05", "price": "100"},
    )
    auth_client.post(
        "/quotes",
        data={"ticker_id": str(ticker_id), "recorded_date": "2026-01-05", "price": "110"},
    )

    with app.app_context():
        rows = db.session.query(QuoteHistory).filter_by(ticker_id=ticker_id).all()
        assert len(rows) == 1
        assert rows[0].price == Decimal("110.00000000")


def test_create_quote_history_entry_rejects_negative_price(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker()

    response = auth_client.post(
        "/quotes",
        data={"ticker_id": str(ticker_id), "recorded_date": "2026-01-05", "price": "-1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "não pode ser negativo".encode() in response.data
    with app.app_context():
        assert db.session.query(QuoteHistory).count() == 0


def test_delete_quote_history_entry_by_ticker_and_date(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker()

    auth_client.post(
        "/quotes",
        data={"ticker_id": str(ticker_id), "recorded_date": "2026-01-05", "price": "100"},
    )
    response = auth_client.post(
        "/quotes/delete-by-date",
        data={"ticker_id": str(ticker_id), "recorded_date": "2026-01-05"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert db.session.query(QuoteHistory).filter_by(ticker_id=ticker_id).count() == 0


@pytest.mark.security
def test_quotes_report_requires_authentication(client: FlaskClient) -> None:
    assert client.get("/quotes").status_code == 302


def _seed_price(ticker_id: int, day: str, price: str) -> None:
    db.session.add(
        QuoteHistory(
            ticker_id=ticker_id,
            price=Decimal(price),
            recorded_date=date.fromisoformat(day),
            recorded_at=datetime.combine(date.fromisoformat(day), time.min, tzinfo=UTC),
        )
    )
    db.session.commit()


def test_quotes_offers_and_applies_benchmark_comparison(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker()
        _seed_price(ticker_id, "2026-01-05", "125000")
        benchmark = Ticker(
            symbol="BOVA11",
            trading_name="iShares Bovespa",
            market=Market.B3,
            rtd_market_code="B",
            currency="BRL",
        )
        db.session.add(benchmark)
        db.session.commit()
        benchmark_id = benchmark.id
        _seed_price(benchmark_id, "2026-01-05", "108")

    page = auth_client.get(f"/quotes?ticker_id={ticker_id}&benchmark_ticker_id={benchmark_id}")

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert f'<option value="{benchmark_id}" selected>BOVA11</option>' in html
    assert 'data-benchmark-label="BOVA11"' in html
    assert "data-benchmark-dates=" in html
    assert "108" in html


def test_quotes_excludes_selected_ticker_from_its_own_benchmark_options(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        benchmark = Ticker(
            symbol="BOVA11",
            trading_name="iShares Bovespa",
            market=Market.B3,
            rtd_market_code="B",
            currency="BRL",
        )
        db.session.add(benchmark)
        db.session.commit()
        benchmark_id = benchmark.id
        _seed_price(benchmark_id, "2026-01-05", "108")

    page = auth_client.get(f"/quotes?ticker_id={benchmark_id}")

    assert page.status_code == 200
    assert "Comparar com" not in page.get_data(as_text=True)


def test_quotes_hides_comparison_control_without_candidates(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker()
        _seed_price(ticker_id, "2026-01-05", "125000")

    page = auth_client.get(f"/quotes?ticker_id={ticker_id}")

    assert page.status_code == 200
    assert "Comparar com" not in page.get_data(as_text=True)
