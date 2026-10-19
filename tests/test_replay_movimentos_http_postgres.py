"""Correções HTTP recalculam posição, snapshots e resultados já realizados."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_financial_isolation_http import client_for
from test_financial_isolation_http import review_case as review_case
from test_position_use_cases import _command

from app import db
from app.models import Position, PositionMovementKind, Side, Transaction, TransactionStatus
from app.positions.use_cases import close_stock, register_stock

pytestmark = pytest.mark.banco


def cadeia(app, dados, lado=Side.BUY, quantidade_fechada="40"):
    with app.app_context():
        dono = dados["user_ids"][0]
        comando = replace(_command(dados, "stock"), side=lado)
        pid = register_stock(comando, owner_id=dono, token=uuid4().hex).position.id
        register_stock(replace(comando, average_cost=Decimal("20"), opened_on=date(2026, 6, 20)),
                       owner_id=dono, token=uuid4().hex)
        parcial = close_stock(pid, Decimal("25"), date(2026, 6, 30), Decimal(quantidade_fechada), owner_id=dono)
        posicao = db.session.get(Position, pid)
        ids = {m.kind: m.id for m in posicao.movements}
        return pid, ids, parcial.id


def atualizar(cliente, token, pid, mid, quantidade, preco, dia, **extras):
    return cliente.post(f"/positions/{pid}/movements/{mid}", data={
        "csrf_token": token, "quantity": quantidade, "price": preco, "occurred_on": dia, **extras})


def fotografia(app, pid):
    with app.app_context():
        p = db.session.get(Position, pid)
        movimentos = [(m.id, m.kind, m.quantity_delta, m.price, m.occurred_on,
                       m.resulting_quantity, m.resulting_average_cost, m.result) for m in p.movements]
        transacoes = [(t.id, t.status, t.quantity, t.average_cost, t.exit_price, t.opened_on, t.closed_on, t.result)
                      for t in db.session.scalars(select(Transaction).where(Transaction.owner_id == p.owner_id).order_by(Transaction.id))]
        return p.quantity, p.average_cost, p.opened_on, movimentos, transacoes


@pytest.mark.parametrize("lado,sinal", [(Side.BUY, 1), (Side.SELL, -1)])
def test_corrigir_aumento_e_baixa_recalcula_resultados_e_desfazer_restaura_saldo(review_case, lado, sinal):
    app, dados = review_case
    pid, ids, tid = cadeia(app, dados, lado)
    cliente, token = client_for(app, dados["ids"][0])
    assert atualizar(cliente, token, pid, ids[PositionMovementKind.INCREASE], "100", "30", "2026-06-20").status_code == 302
    with app.app_context():
        p, t = db.session.get(Position, pid), db.session.get(Transaction, tid)
        assert (p.quantity, p.average_cost) == (Decimal("160"), Decimal("20"))
        assert (t.average_cost, t.result) == (Decimal("20"), Decimal(200 * sinal))
    assert atualizar(cliente, token, pid, ids[PositionMovementKind.DECREASE], "50", "26", "2026-06-29").status_code == 302
    with app.app_context():
        p, t = db.session.get(Position, pid), db.session.get(Transaction, tid)
        assert p.quantity == Decimal("150")
        assert (t.quantity, t.closed_on, t.result) == (Decimal("50"), date(2026, 6, 29), Decimal(300 * sinal))
        assert p.movements[-1].result == t.result
    assert cliente.post(f"/positions/{pid}/movements/{ids[PositionMovementKind.INCREASE]}/delete", data={"csrf_token": token}).status_code == 302
    with app.app_context():
        p, t = db.session.get(Position, pid), db.session.get(Transaction, tid)
        assert (p.quantity, p.average_cost, t.result) == (Decimal("50"), Decimal("10"), Decimal(800 * sinal))
    assert cliente.post(f"/positions/{pid}/movements/{ids[PositionMovementKind.DECREASE]}/delete", data={"csrf_token": token}).status_code == 302
    with app.app_context():
        p = db.session.get(Position, pid)
        assert (p.quantity, p.average_cost, len(p.movements)) == (Decimal("100"), Decimal("10"), 1)
        assert db.session.get(Transaction, tid) is None
        espelho = db.session.scalar(select(Transaction).where(Transaction.owner_id == p.owner_id))
        assert espelho.status is TransactionStatus.OPEN and espelho.quantity == p.quantity


@pytest.mark.parametrize("acao", ["editar", "excluir"])
def test_correcao_que_deixa_baixa_sem_saldo_reverte_todo_o_extrato(review_case, acao):
    app, dados = review_case
    pid, ids, _ = cadeia(app, dados, quantidade_fechada="140")
    antes = fotografia(app, pid)
    cliente, token = client_for(app, dados["ids"][0])
    mid = ids[PositionMovementKind.INCREASE]
    if acao == "editar":
        resposta = atualizar(cliente, token, pid, mid, "20", "20", "2026-06-20")
    else:
        resposta = cliente.post(f"/positions/{pid}/movements/{mid}/delete", data={"csrf_token": token})
    assert resposta.status_code == 302
    assert fotografia(app, pid) == antes


@pytest.mark.parametrize("lado,sinal", [(Side.BUY, 1), (Side.SELL, -1)])
def test_remover_abertura_promove_aporte_e_corrige_custo_e_datas_da_baixa(review_case, lado, sinal):
    app, dados = review_case
    pid, ids, tid = cadeia(app, dados, lado)
    cliente, token = client_for(app, dados["ids"][0])
    assert cliente.post(f"/positions/{pid}/movements/{ids[PositionMovementKind.OPEN]}/delete", data={"csrf_token": token}).status_code == 302
    with app.app_context():
        p, t = db.session.get(Position, pid), db.session.get(Transaction, tid)
        assert (p.quantity, p.average_cost, p.opened_on) == (Decimal("60"), Decimal("20"), date(2026, 6, 20))
        assert p.movements[0].id == ids[PositionMovementKind.INCREASE]
        assert p.movements[0].kind is PositionMovementKind.OPEN
        assert (t.average_cost, t.result, t.opened_on) == (Decimal("20"), Decimal(200 * sinal), p.opened_on)


def test_antecipar_aporte_exige_confirmacao_e_depois_reordena_sem_perder_resultados(review_case):
    app, dados = review_case
    pid, ids, tid = cadeia(app, dados)
    antes = fotografia(app, pid)
    cliente, token = client_for(app, dados["ids"][0])
    mid = ids[PositionMovementKind.INCREASE]
    assert atualizar(cliente, token, pid, mid, "100", "20", "2026-06-01").status_code == 409
    assert fotografia(app, pid) == antes
    assert atualizar(cliente, token, pid, mid, "100", "20", "2026-06-01", confirm_prior_opening="1").status_code == 302
    with app.app_context():
        p, t = db.session.get(Position, pid), db.session.get(Transaction, tid)
        assert (p.quantity, p.average_cost, p.opened_on) == (Decimal("160"), Decimal("15"), date(2026, 6, 1))
        assert p.movements[0].id == mid and p.movements[0].kind is PositionMovementKind.OPEN
        assert t.opened_on == p.opened_on and t.result == Decimal("400")


def test_administrador_alheio_nao_pode_reescrever_movimento_financeiro(review_case):
    app, dados = review_case
    pid, ids, _ = cadeia(app, dados)
    antes = fotografia(app, pid)
    cliente, token = client_for(app, dados["ids"][1])
    mid = ids[PositionMovementKind.OPEN]
    assert atualizar(cliente, token, pid, mid, "999", "1", "2026-06-10").status_code == 404
    assert cliente.post(f"/positions/{pid}/movements/{mid}/delete", data={"csrf_token": token}).status_code == 404
    assert fotografia(app, pid) == antes
