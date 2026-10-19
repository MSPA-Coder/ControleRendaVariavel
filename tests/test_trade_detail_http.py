"""Leituras da carteira preservam dono, disponibilidade e métricas do domínio."""

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from flask import template_rendered
from test_financial_isolation_http import client_for
from test_financial_isolation_http import review_case as review_case

from app import db
from app.models import OptionQuote, Position, Quote, Side

pytestmark = pytest.mark.banco


@contextmanager
def rendered_context(app):
    contexts = []

    def receive(sender, template, context, **extra):
        contexts.append(context)

    template_rendered.connect(receive, app)
    try:
        yield contexts
    finally:
        template_rendered.disconnect(receive, app)


@pytest.mark.parametrize("actor", [0, 1])
def test_detalhe_nao_revela_posicao_de_outro_dono(review_case, actor):
    app, data = review_case
    client, _ = client_for(app, data["ids"][actor])
    for path in (f"/positions/{data['position']}", f"/options/positions/{data['option']}"):
        assert client.get(path).status_code == 404
        assert client.get(path, headers={"HX-Request": "true"}).status_code == 404


def test_opcao_sem_cotacao_preserva_cadastro_e_nao_inventa_metricas(review_case):
    app, data = review_case
    client, _ = client_for(app, data["ids"][2])
    with rendered_context(app) as contexts:
        response = client.get(f"/options/positions/{data['option']}")
    assert response.status_code == 200
    item = contexts[-1]["item"]
    assert item.position.id == data["option"]
    assert item.position.contract.strike == Decimal("10")
    assert item.metrics is None
    assert item.greeks is None


def test_leitura_do_detalhe_da_opcao_preserva_a_posicao_e_usa_resultado_do_contrato(review_case):
    app, data = review_case
    with app.app_context():
        db.session.add(OptionQuote(contract_id=data["contract"], last_price=Decimal("3"),
                                   previous_close=Decimal("2"), underlying_price=Decimal("12"),
                                   observed_at=datetime.now(UTC)))
        db.session.commit()
    client, _ = client_for(app, data["ids"][2])
    path = f"/options/positions/{data['option']}"
    with rendered_context(app) as contexts:
        response = client.get(path)
    assert response.status_code == 200
    assert contexts[-1]["item"].metrics.result == Decimal("19")
    assert contexts[-1]["item"].metrics.notional == 0
    with app.app_context():
        from app.models import OptionPosition
        assert db.session.get(OptionPosition, data["option"]).quantity == Decimal("19")
        assert db.session.get(OptionPosition, data["option"]).average_cost == Decimal("2")


def test_peso_no_detalhe_considera_as_outras_posicoes_da_carteira(review_case):
    app, data = review_case
    with app.app_context():
        db.session.add_all([
            Quote(ticker_id=data["ticker"], last_price=Decimal("30"),
                  previous_close=Decimal("29"), observed_at=datetime.now(UTC)),
            Quote(ticker_id=data["ticker_ids"][2], last_price=Decimal("20"),
                  previous_close=Decimal("19"), observed_at=datetime.now(UTC)),
            Position(owner_id=data["user_ids"][2], broker_id=data["broker"],
                     ticker_id=data["ticker_ids"][2], portfolio_id=data["portfolio"],
                     quantity=Decimal("1"), average_cost=Decimal("10"),
                     target_multiplier=1, side=Side.BUY, opened_on=datetime.now(UTC).date()),
        ])
        db.session.commit()
    client, _ = client_for(app, data["ids"][2])
    with rendered_context(app) as contexts:
        response = client.get(f"/positions/{data['position']}")
    assert response.status_code == 200
    item = contexts[-1]["item"]
    assert item.current_weight == Decimal("510") / Decimal("530")
    assert item.metrics.result == Decimal("119")


@pytest.mark.parametrize("path", ["/", "/options"])
def test_leitura_completa_e_fragmento_expoem_apenas_as_posicoes_do_dono(review_case, path):
    app, data = review_case
    client, _ = client_for(app, data["ids"][2])
    for headers in ({}, {"HX-Request": "true"}):
        with rendered_context(app) as contexts:
            response = client.get(path, headers=headers)
        assert response.status_code == 200
        items = contexts[-1]["portfolio"].positions
        assert len(items) == 1
        assert items[0].position.owner_id == data["user_ids"][2]
