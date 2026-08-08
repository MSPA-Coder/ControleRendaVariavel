from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Broker, Dividend, Market, Ticker

pytestmark = [pytest.mark.observable_contract]

HTMX = {"HX-Request": "true"}


def _seed() -> tuple[int, int]:
    broker = Broker(name="XP", acronym="XP")
    other = Broker(name="Genial", acronym="GEN")
    ticker = Ticker(
        symbol="PETR4",
        trading_name="Petrobras",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    db.session.add_all([broker, other, ticker])
    db.session.commit()
    db.session.add(
        Dividend(
            broker_id=broker.id,
            ticker_id=ticker.id,
            amount=Decimal("12.50"),
            payment_date=date(2026, 2, 15),
        )
    )
    db.session.commit()
    return broker.id, other.id


@pytest.mark.security
@pytest.mark.critical
def test_partial_requires_authentication(client: FlaskClient) -> None:
    """`HX-Request` muda a forma da resposta, nunca a autorização."""
    assert client.get("/dividends", headers=HTMX).status_code == 302


@pytest.mark.critical
@pytest.mark.business_rule
def test_partial_and_page_show_the_same_numbers(app: Flask, auth_client: FlaskClient) -> None:
    """O fragmento e a página inteira saem do mesmo contexto e do mesmo
    template. Se divergirem, a atualização via HTMX passaria a mostrar
    números diferentes dos que o usuário viu ao abrir a página."""
    with app.app_context():
        _seed()

    page = auth_client.get("/dividends").get_data(as_text=True)
    partial = auth_client.get("/dividends", headers=HTMX).get_data(as_text=True)

    assert "PETR4" in page and "PETR4" in partial
    assert "12,50" in page and "12,50" in partial


def test_htmx_response_is_a_fragment_not_the_whole_page(
    app: Flask, auth_client: FlaskClient
) -> None:
    """A mesma URL serve a página e o fragmento; sem isso o filtro empurraria
    ao histórico o endereço de um fragmento em vez do da página."""
    with app.app_context():
        _seed()

    fragment = auth_client.get("/dividends", headers=HTMX).get_data(as_text=True)
    page = auth_client.get("/dividends").get_data(as_text=True)

    assert "<!doctype html>" not in fragment.lower()
    assert "<nav" not in fragment.lower()
    assert "<!doctype html>" in page.lower()
    assert 'id="dividends-results"' in page
    assert 'id="dividends-results"' in fragment


@pytest.mark.business_rule
def test_filter_actually_filters(app: Flask, auth_client: FlaskClient) -> None:
    """Um filtro aplicado precisa realmente restringir a consulta, não só o
    valor pré-selecionado no <select>."""
    with app.app_context():
        _seed()

    html = auth_client.get("/dividends?broker=Genial", headers=HTMX).get_data(as_text=True)

    assert "Nenhum provento registrado" in html
    assert "PETR4" not in html
