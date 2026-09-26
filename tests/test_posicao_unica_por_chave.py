"""Uma posição por chave (dono, carteira, corretora, ativo, lado).

O aporte já fundia na posição existente, mas o `FOR UPDATE` não tem o que
travar quando ela ainda não existe: dois cliques no primeiro aporte criavam
duas posições. A edição podia mover uma posição para a chave de outra. Agora o
banco recusa (`uq_positions_chave`), o aporte serializa por lock consultivo e a
edição que colide é recusada antes do banco.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Broker, Market, Portfolio, Position, Side, Ticker, User
from app.positions.closure import conflicting_position, create_or_merge_position
from tests.test_financial_isolation_http import client_for
from tests.test_financial_isolation_http import review_case as review_case

pytestmark = pytest.mark.banco


@pytest.fixture
def cenario(sessao):
    usuario = User(username="chave", password_hash="hash")
    corretora = Broker(name="Corretora chave", acronym="CCHV")
    outra = Broker(name="Outra corretora chave", acronym="OCHV")
    papel = Ticker(
        symbol="CHVE3", trading_name="Chave S.A.", market=Market.B3,
        rtd_market_code="B", currency="BRL",
    )
    carteira = Portfolio(name="Carteira chave", owner_ref=usuario, currency="BRL")
    sessao.add_all([usuario, corretora, outra, papel, carteira])
    sessao.flush()
    return usuario, corretora, outra, papel, carteira


def _posicao(cenario, corretora=None, quantidade="100", custo="10.00"):
    usuario, padrao, _outra, papel, carteira = cenario
    return Position(
        owner_id=usuario.id, broker_id=(corretora or padrao).id, ticker_id=papel.id,
        portfolio_id=carteira.id, quantity=Decimal(quantidade), average_cost=Decimal(custo),
        side=Side.BUY, opened_on=date(2026, 6, 10),
    )


def test_banco_recusa_segunda_posicao_na_mesma_chave(sessao, cenario):
    sessao.add(_posicao(cenario))
    sessao.flush()
    with sessao.begin_nested(), pytest.raises(IntegrityError, match="uq_positions_chave"):
        sessao.add(_posicao(cenario))
        sessao.flush()


def test_outra_corretora_e_outra_posicao(sessao, cenario):
    _usuario, _corretora, outra, _papel, _carteira = cenario
    sessao.add_all([_posicao(cenario), _posicao(cenario, corretora=outra)])
    sessao.flush()


def test_segundo_aporte_reforca_a_primeira(sessao, cenario):
    primeira, existia = create_or_merge_position(_posicao(cenario, quantidade="100", custo="10.00"))
    segunda, existia_depois = create_or_merge_position(_posicao(cenario, quantidade="100", custo="20.00"))

    assert (existia, existia_depois) == (False, True)
    assert segunda.id == primeira.id
    assert segunda.quantity == Decimal("200")
    assert segunda.average_cost == Decimal("15.00")
    assert sessao.query(Position).filter_by(ticker_id=primeira.ticker_id).count() == 1


def test_tela_recusa_edicao_que_colide_com_422(review_case):
    """Sem a checagem, o índice único responderia com erro 500."""
    app, data = review_case
    client, token = client_for(app, data["ids"][0])
    form = {
        "csrf_token": token, "broker_id": data["broker"], "ticker_id": data["ticker_ids"][0],
        "portfolio_id": data["portfolio_ids"][0], "quantity": "4", "average_cost": "10", "side": "C",
        "opened_on": date.today().isoformat(), "target_multiplier": "1.5",
    }
    assert client.post("/positions", data=form).status_code == 302
    assert client.post("/positions", data={**form, "ticker_id": data["ticker_ids"][2]}).status_code == 302
    with app.app_context():
        movida = db.session.query(Position).filter_by(
            owner_id=data["user_ids"][0], ticker_id=data["ticker_ids"][2]
        ).one()

    response = client.post(f"/positions/{movida.id}", data=form)

    assert response.status_code == 422
    # O toast leva a mensagem em JSON num atributo, com os acentos escapados.
    assert "existe uma posi" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Position, movida.id).ticker_id == data["ticker_ids"][2]


def test_edicao_que_colide_e_detectada(sessao, cenario):
    _usuario, corretora, outra, _papel, _carteira = cenario
    sessao.add(_posicao(cenario))
    movida = _posicao(cenario, corretora=outra)
    sessao.add(movida)
    sessao.flush()

    assert conflicting_position(movida) is None
    movida.broker_id = corretora.id
    assert conflicting_position(movida) is not None
