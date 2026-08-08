from __future__ import annotations

import pytest
from flask.testing import FlaskClient

pytestmark = [pytest.mark.interface_smoke, pytest.mark.observable_contract]


def test_each_reference_table_has_its_own_page(auth_client: FlaskClient) -> None:
    pages = {
        "/tables/brokers": "Corretoras",
        "/tables/tickers": "Tickers",
        "/tables/options/expirations": "Vencimentos de calls e puts",
        "/tables/options/contracts": "Contratos de opções",
    }
    for url, heading in pages.items():
        response = auth_client.get(url)
        assert response.status_code == 200, url
        html = response.get_data(as_text=True)
        assert heading in html


def test_broker_save_returns_to_the_brokers_page(auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/tables/brokers",
        data={"name": "Genial", "acronym": "GE"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/tables/brokers")


def test_ticker_save_returns_to_the_tickers_page(auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/tables/tickers",
        data={
            "symbol": "ITUB4",
            "trading_name": "Itaú Unibanco PN",
            "market": "B3",
            "rtd_market_code": "B",
            "currency": "BRL",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/tables/tickers")


def test_expiration_save_returns_to_the_expirations_page(auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/tables/options/expirations",
        data={"call_code": "2027A", "put_code": "2027M", "exercise_date": "2027-01-18"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/tables/options/expirations")
