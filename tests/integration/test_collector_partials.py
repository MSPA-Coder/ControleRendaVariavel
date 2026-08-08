from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Broker, Market, Position, PositionKind, Quote, Side, Ticker

pytestmark = [pytest.mark.observable_contract]

HEARTBEAT_URL = "/partials/collector-heartbeat"
RTD_URL = "/partials/rtd-service"


def _seed_position_with_quote(observed_at: datetime, source_status: str = "online") -> None:
    broker = Broker(name="XP", acronym="XP")
    ticker = Ticker(
        symbol="PETR4",
        trading_name="Petrobras",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    db.session.add_all([broker, ticker])
    db.session.commit()
    position = Position(
        broker_id=broker.id,
        ticker_id=ticker.id,
        quantity=Decimal("100"),
        average_cost=Decimal("20"),
        side=Side.BUY,
        opened_on=datetime.now(UTC).date(),
        quote_multiplier=Decimal("1"),
        target_multiplier=Decimal("1.5"),
        result_mode="L",
        position_kind=PositionKind.REAL,
    )
    db.session.add(position)
    db.session.commit()
    db.session.add(
        Quote(
            position_id=position.id,
            last_price=Decimal("25"),
            previous_close=Decimal("25"),
            source_status=source_status,
            observed_at=observed_at,
        )
    )
    db.session.commit()


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.parametrize("url", [HEARTBEAT_URL, RTD_URL])
def test_partials_require_authentication(client: FlaskClient, url: str) -> None:
    """Fragmentos carregam os mesmos dados das páginas e exigem a mesma
    sessão. Esta asserção substitui a que existia nos endpoints JSON."""
    assert client.get(url).status_code == 302


@pytest.mark.security
@pytest.mark.critical
def test_htmx_header_does_not_bypass_authentication(client: FlaskClient) -> None:
    """`HX-Request` é negociação de apresentação, nunca autorização."""
    response = client.get(HEARTBEAT_URL, headers={"HX-Request": "true"})

    assert response.status_code == 302


def test_heartbeat_partial_reports_waiting_without_readings(auth_client: FlaskClient) -> None:
    html = auth_client.get(HEARTBEAT_URL).get_data(as_text=True)

    assert "is-waiting" in html
    assert "Sem leitura registrada" in html


def test_heartbeat_partial_reports_online_for_a_fresh_reading(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_position_with_quote(datetime.now(UTC))

    html = auth_client.get(HEARTBEAT_URL).get_data(as_text=True)

    assert "is-online" in html
    assert "Coletor online" in html


def test_heartbeat_partial_reports_stale_for_an_old_reading(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_position_with_quote(datetime.now(UTC) - timedelta(days=1))

    html = auth_client.get(HEARTBEAT_URL).get_data(as_text=True)

    assert "is-stale" in html
    assert "Coletor atrasado" in html


def test_heartbeat_partial_keeps_polling_itself(auth_client: FlaskClient) -> None:
    """O fragmento devolve seus próprios atributos hx-*; sem isso o ciclo de
    atualização morreria após a primeira troca."""
    html = auth_client.get(HEARTBEAT_URL).get_data(as_text=True)

    assert 'hx-trigger="every 10s"' in html
    assert 'hx-swap="outerHTML"' in html
    assert HEARTBEAT_URL in html


def test_rtd_partial_renders_the_toggle(auth_client: FlaskClient) -> None:
    html = auth_client.get(RTD_URL).get_data(as_text=True)

    assert 'class="rtd-toggle"' in html
    assert 'role="switch"' in html
    assert RTD_URL in html


def test_rtd_partial_survives_an_unavailable_controller(auth_client: FlaskClient) -> None:
    """O controlador RTD é externo: indisponibilidade vira estado visível,
    não erro 500."""
    response = auth_client.get(RTD_URL)

    assert response.status_code == 200
    assert "disabled" in response.get_data(as_text=True)


def test_rtd_toggle_post_returns_the_updated_fragment(auth_client: FlaskClient) -> None:
    response = auth_client.post(RTD_URL, data={})

    assert response.status_code == 200
    assert 'class="rtd-toggle"' in response.get_data(as_text=True)
