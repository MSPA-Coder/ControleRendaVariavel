from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import AppSetting, Broker, Market, Position, PositionKind, Quote, Side, Ticker

pytestmark = [pytest.mark.observable_contract]

PARTIAL_URL = "/partials/portfolio"


def _seed() -> None:
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
    position = Position(
        broker_id=broker.id,
        ticker_id=ticker.id,
        quantity=Decimal("100"),
        average_cost=Decimal("20"),
        side=Side.BUY,
        opened_on=date(2026, 1, 1),
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
            source_status="online",
            observed_at=datetime.now(UTC),
        )
    )
    db.session.commit()


@pytest.mark.security
@pytest.mark.critical
def test_portfolio_partial_requires_authentication(client: FlaskClient) -> None:
    assert client.get(PARTIAL_URL).status_code == 302


@pytest.mark.critical
@pytest.mark.business_rule
def test_partial_and_page_show_the_same_numbers(app: Flask, auth_client: FlaskClient) -> None:
    """O fragmento e a página inteira saem do mesmo contexto e do mesmo
    template. Se divergirem, o auto-refresh passaria a mostrar números
    diferentes dos que o usuário viu ao abrir a página."""
    with app.app_context():
        _seed()

    page = auth_client.get("/").get_data(as_text=True)
    partial = auth_client.get(PARTIAL_URL).get_data(as_text=True)

    # Resultado líquido de 100 x (25 - 20), com o fator de corretagem.
    assert "PETR4" in page and "PETR4" in partial
    assert "499,80" in page and "499,80" in partial


def test_partial_keeps_polling_itself(app: Flask, auth_client: FlaskClient) -> None:
    """Os atributos hx-* voltam no fragmento; sem isso a atualização
    automática pararia depois da primeira troca."""
    with app.app_context():
        _seed()

    html = auth_client.get(PARTIAL_URL).get_data(as_text=True)

    assert 'id="portfolio-results"' in html
    assert "hx-trigger=" in html and "every" in html
    assert 'hx-swap="outerHTML"' in html


@pytest.mark.business_rule
def test_partial_preserves_the_active_filters(app: Flask, auth_client: FlaskClient) -> None:
    """A atualização automática não pode descartar o recorte escolhido: a
    URL de polling carrega os filtros vigentes."""
    with app.app_context():
        _seed()

    html = auth_client.get(f"{PARTIAL_URL}?broker=Genial").get_data(as_text=True)

    assert "Nenhuma posição encontrada" in html
    assert "broker=Genial" in html


@pytest.mark.business_rule
def test_polling_interval_follows_the_configured_value(
    app: Flask, auth_client: FlaskClient
) -> None:
    """O intervalo vem do banco, então mudá-lo passa a valer na troca
    seguinte — sem recarregar a página, como o JavaScript anterior fazia."""
    with app.app_context():
        _seed()
        settings = db.session.get(AppSetting, 1)
        assert settings is not None
        settings.poll_interval_seconds = 37
        db.session.commit()

    html = auth_client.get(PARTIAL_URL).get_data(as_text=True)

    assert 'hx-trigger="every 37s"' in html


def test_page_no_longer_carries_the_full_reload_hooks(
    app: Flask, auth_client: FlaskClient
) -> None:
    """O auto-refresh antigo lia estes data-* e recarregava a página
    inteira; eles não devem sobreviver à conversão."""
    with app.app_context():
        _seed()

    html = auth_client.get("/").get_data(as_text=True)

    assert "data-refresh-seconds" not in html
    assert "data-refresh-api" not in html
