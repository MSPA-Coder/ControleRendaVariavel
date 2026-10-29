"""A data da posição e seu histórico financeiro devem contar a mesma história."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from test_financial_isolation_http import client_for
from test_financial_isolation_http import review_case as review_case
from test_position_use_cases import _command

from app import db
from app.models import (
    Broker,
    OptionContract,
    OptionPosition,
    OptionType,
    Portfolio,
    Position,
    PositionMovementKind,
    Transaction,
)
from app.positions.use_cases import close_option, close_stock, register_option, register_stock

pytestmark = pytest.mark.banco


@pytest.mark.parametrize("instrumento", ["stock", "option"])
@pytest.mark.parametrize("nova_data,aceita", [("2026-06-01", True), ("2026-06-25", False)])
def test_edicao_da_data_atualiza_abertura_e_vendas_ou_recusa_cronologia(review_case, instrumento, nova_data, aceita):
    app, dados = review_case
    registrar = register_stock if instrumento == "stock" else register_option
    encerrar = close_stock if instrumento == "stock" else close_option
    modelo = Position if instrumento == "stock" else OptionPosition
    with app.app_context():
        dono = dados["user_ids"][0]
        comando = _command(dados, instrumento)
        pid = registrar(comando, owner_id=dono, token=uuid4().hex).position.id
        registrar(replace(comando, opened_on=date(2026, 6, 20), average_cost=Decimal("20")),
                  owner_id=dono, token=uuid4().hex)
        tid = encerrar(pid, Decimal("25"), date(2026, 6, 30), Decimal("40"), owner_id=dono).id
        posicao = db.session.get(modelo, pid)
        form = {"broker_id": posicao.broker_id, "portfolio_id": posicao.portfolio_id,
                "quantity": str(posicao.quantity), "average_cost": str(posicao.average_cost),
                "side": posicao.side.value, "opened_on": nova_data, "edit_version": str(posicao.edit_version)}
        if instrumento == "stock":
            form.update(ticker_id=posicao.ticker_id, target_multiplier="1.5")
        else:
            form.update(contract_id=posicao.contract_id, target_price="")
    cliente, token = client_for(app, dados["ids"][0])
    caminho = f"/positions/{pid}" if instrumento == "stock" else f"/options/positions/{pid}"
    resposta = cliente.post(caminho, data={**form, "csrf_token": token})
    assert resposta.status_code == (302 if aceita else 422)
    with app.app_context():
        posicao = db.session.get(modelo, pid)
        abertura = next(m for m in posicao.movements if m.kind is PositionMovementKind.OPEN)
        venda = db.session.get(Transaction, tid)
        esperado = date.fromisoformat(nova_data) if aceita else date(2026, 6, 10)
        assert posicao.opened_on == abertura.occurred_on == venda.opened_on == esperado
        assert posicao.quantity == Decimal("160")
        assert venda.closed_on == date(2026, 6, 30) and venda.result == Decimal("400")


@pytest.mark.parametrize("instrumento", ["stock", "option"])
@pytest.mark.parametrize("campo", ["broker_id", "portfolio_id", "instrumento", "side"])
def test_edicao_nao_muda_identidade_de_posicao_com_encerramento_parcial(review_case, instrumento, campo):
    app, dados = review_case
    registrar = register_stock if instrumento == "stock" else register_option
    encerrar = close_stock if instrumento == "stock" else close_option
    modelo = Position if instrumento == "stock" else OptionPosition
    with app.app_context():
        dono = dados["user_ids"][0]
        posicao = registrar(_command(dados, instrumento), owner_id=dono, token=uuid4().hex).position
        pid = posicao.id
        tid = encerrar(pid, Decimal("25"), date(2026, 6, 30), Decimal("40"), owner_id=dono).id
        posicao = db.session.get(modelo, pid)
        corretora = Broker(name=f"Outra {uuid4().hex}", acronym=uuid4().hex[:6])
        carteira = Portfolio(owner_id=dono, name=f"Outra {uuid4().hex}", currency="BRL")
        contrato = OptionContract(ticker_id=dados["ticker_ids"][2], underlying_ticker_id=dados["ticker_ids"][0],
                                  expiration_id=dados["expiration"], option_type=OptionType.CALL, strike=Decimal("10"))
        db.session.add_all([corretora, carteira, contrato])
        db.session.commit()
        extras_ids = corretora.id, carteira.id, contrato.id
        form = {"broker_id": posicao.broker_id, "portfolio_id": posicao.portfolio_id,
                "quantity": str(posicao.quantity), "average_cost": str(posicao.average_cost),
                "side": posicao.side.value, "opened_on": posicao.opened_on.isoformat(),
                "edit_version": str(posicao.edit_version)}
        if instrumento == "stock":
            form.update(ticker_id=posicao.ticker_id, target_multiplier="1.5")
        else:
            form.update(contract_id=posicao.contract_id, target_price="")
        chave = ("ticker_id" if instrumento == "stock" else "contract_id") if campo == "instrumento" else campo
        alternativas = {"broker_id": extras_ids[0], "portfolio_id": extras_ids[1],
                        "ticker_id": dados["ticker_ids"][2], "contract_id": extras_ids[2], "side": "V"}
        original = form[chave]
        form[chave] = alternativas[chave]
    cliente, token = client_for(app, dados["ids"][0])
    caminho = f"/positions/{pid}" if instrumento == "stock" else f"/options/positions/{pid}"
    try:
        resposta = cliente.post(caminho, data={**form, "csrf_token": token})
        assert resposta.status_code == 422
        with app.app_context():
            posicao = db.session.get(modelo, pid)
            atual = getattr(posicao, chave)
            assert (atual.value if chave == "side" else atual) == original
            assert db.session.get(Transaction, tid).result == Decimal("600")
    finally:
        with app.app_context():
            posicao = db.session.get(modelo, pid)
            if chave == "side":
                from app.models import Side
                posicao.side = Side(original)
            else:
                setattr(posicao, chave, original)
            db.session.flush()
            db.session.delete(db.session.get(OptionContract, extras_ids[2]))
            db.session.delete(db.session.get(Portfolio, extras_ids[1]))
            db.session.delete(db.session.get(Broker, extras_ids[0]))
            db.session.commit()


@pytest.mark.parametrize("instrumento", ["stock", "option"])
def test_encerramento_legado_sem_movimento_tambem_impede_mudar_lado(review_case, instrumento):
    app, dados = review_case
    registrar = register_stock if instrumento == "stock" else register_option
    encerrar = close_stock if instrumento == "stock" else close_option
    modelo = Position if instrumento == "stock" else OptionPosition
    with app.app_context():
        dono = dados["user_ids"][0]
        pid = registrar(_command(dados, instrumento), owner_id=dono, token=uuid4().hex).position.id
        tid = encerrar(pid, Decimal("25"), date(2026, 6, 30), Decimal("40"), owner_id=dono).id
        posicao = db.session.get(modelo, pid)
        posicao.movements.remove(next(m for m in posicao.movements if m.kind is PositionMovementKind.DECREASE))
        db.session.commit()
        form = {"broker_id": posicao.broker_id, "portfolio_id": posicao.portfolio_id,
                "quantity": str(posicao.quantity), "average_cost": str(posicao.average_cost),
                "side": "V", "opened_on": posicao.opened_on.isoformat(),
                "edit_version": str(posicao.edit_version)}
        if instrumento == "stock":
            form.update(ticker_id=posicao.ticker_id, target_multiplier="1.5")
        else:
            form.update(contract_id=posicao.contract_id, target_price="")
    cliente, token = client_for(app, dados["ids"][0])
    caminho = f"/positions/{pid}" if instrumento == "stock" else f"/options/positions/{pid}"
    assert cliente.post(caminho, data={**form, "csrf_token": token}).status_code == 422
    with app.app_context():
        assert db.session.get(modelo, pid).side.value == "C"
        assert db.session.get(Transaction, tid).result == Decimal("600")
