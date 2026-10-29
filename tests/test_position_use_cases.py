"""Comandos fora do HTTP continuam atômicos e isolados por dono.

Falha real de precisão no PostgreSQL ocorre depois de a chave e o aporte
serem iniciados. Nada pode sobreviver, nem impedir retry corrigido.
"""

import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import suppress
from dataclasses import replace
from datetime import date
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError

from app import db
from app.models import OptionPosition, Position, PositionSubmission, Side, Transaction
from app.options import closure as options
from app.positions import closure as stocks
from app.positions.commands import OptionPositionInput, PositionInput
from app.positions.queries import option_positions, stock_positions
from app.positions.use_cases import (
    ConfirmationRequiredError,
    close_option,
    close_stock,
    register_option,
    register_stock,
)
from tests.test_financial_isolation_http import client_for
from tests.test_financial_isolation_http import review_case as review_case

pytestmark = pytest.mark.banco


def _command(data, instrument, *, owner=0):
    fields = {
        "broker_id": data["broker"], "portfolio_id": data["portfolio_ids"][owner],
        "quantity": Decimal("100"), "average_cost": Decimal("10"),
        "side": Side.BUY, "opened_on": date(2026, 6, 10),
    }
    if instrument == "stock":
        return PositionInput(**fields, ticker_id=data["ticker_ids"][0], target_multiplier=Decimal("1.5"))
    return OptionPositionInput(**fields, contract_id=data["contract"], target_price=None)


@pytest.mark.parametrize("instrument", ["stock", "option"])
def test_aportes_iguais_simultaneos_exigem_confirmacao(review_case, instrument, monkeypatch):
    app, data = review_case
    register = register_stock if instrument == "stock" else register_option
    model = Position if instrument == "stock" else OptionPosition
    modulo = stocks if instrument == "stock" else options
    primeiro_verificou, liberar = Event(), Event()
    verificar = modulo.duplicate_entry

    def verificar_com_pausa(candidate):
        resultado = verificar(candidate)
        if not primeiro_verificou.is_set():
            primeiro_verificou.set()
            if not liberar.wait(10):
                raise AssertionError("o primeiro aporte não foi liberado")
        return resultado

    monkeypatch.setattr(modulo, "duplicate_entry", verificar_com_pausa)

    def registrar():
        with app.app_context():
            try:
                register(_command(data, instrument), owner_id=data["user_ids"][0], token=uuid4().hex)
                return "gravado"
            except ConfirmationRequiredError:
                return "confirmacao"

    with ThreadPoolExecutor(max_workers=2) as pool:
        primeiro = pool.submit(registrar)
        assert primeiro_verificou.wait(10)
        segundo = pool.submit(registrar)
        try:
            # Se o segundo não espera, termina enquanto a primeira decisão está suspensa.
            with suppress(TimeoutError):
                segundo.result(timeout=1)
        finally:
            liberar.set()
        assert sorted([primeiro.result(timeout=10), segundo.result(timeout=10)]) == ["confirmacao", "gravado"]
    with app.app_context():
        posicao = db.session.query(model).filter_by(owner_id=data["user_ids"][0]).one()
        assert posicao.quantity == Decimal("100")
        assert len(posicao.movements) == 1
        chave = uuid4().hex
        register(_command(data, instrument), owner_id=data["user_ids"][0], token=chave, confirm_duplicate=True)
        register(_command(data, instrument), owner_id=data["user_ids"][0], token=chave, confirm_duplicate=True)
        assert db.session.get(model, posicao.id).quantity == Decimal("200")


@pytest.mark.parametrize("instrument", ["stock", "option"])
def test_edicao_antiga_e_recusada_apos_aporte_e_recarregar_permite_editar(review_case, instrument):
    app, data = review_case
    register = register_stock if instrument == "stock" else register_option
    model = Position if instrument == "stock" else OptionPosition
    owner_id = data["user_ids"][0]
    command = _command(data, instrument)
    with app.app_context():
        position_id = register(command, owner_id=owner_id, token=uuid4().hex).position.id
    client, csrf = client_for(app, data["ids"][0])
    base = "/positions" if instrument == "stock" else "/options/positions"
    def version():
        html = client.get(f"{base}/{position_id}/edit").get_data(as_text=True)
        return re.search(r'name="edit_version" value="(\d+)"', html).group(1)
    old_version = version()
    with app.app_context():
        register(replace(command, quantity=Decimal("50")), owner_id=owner_id, token=uuid4().hex)
    form = {"csrf_token": csrf, "edit_version": old_version, "broker_id": data["broker"],
            "portfolio_id": data["portfolio_ids"][0], "quantity": "100", "average_cost": "10",
            "side": "C", "opened_on": "2026-06-10"}
    if instrument == "stock":
        form.update(ticker_id=data["ticker_ids"][0], target_multiplier="1.5")
    else:
        form.update(contract_id=data["contract"], target_price="2")
    response = client.post(f"{base}/{position_id}", data=form)
    assert response.status_code == 409 and "Recarregue os dados" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(model, position_id).quantity == Decimal("150")
    assert client.post(f"{base}/{position_id}", data={**form, "edit_version": ""}).status_code == 409
    assert client.post(f"{base}/{position_id}", data={**form, "edit_version": version(), "quantity": "150"}).status_code == 302
    old_version = version()
    table = "positions" if instrument == "stock" else "option_positions"
    with app.app_context():
        db.session.execute(text(f"UPDATE {table} SET quantity=quantity+1 WHERE id=:id"), {"id": position_id})
        db.session.execute(text(f"UPDATE {table} SET quantity=quantity-1 WHERE id=:id"), {"id": position_id})
        db.session.commit()
    assert client.post(f"{base}/{position_id}", data={**form, "edit_version": old_version}).status_code == 409


@pytest.mark.parametrize("instrument", ["stock", "option"])
def test_database_failure_rolls_back_entire_command_and_allows_corrected_retry(review_case, instrument):
    app, data = review_case
    register = register_stock if instrument == "stock" else register_option
    model = Position if instrument == "stock" else OptionPosition
    with app.app_context():
        owner_id = data["user_ids"][0]
        command = _command(data, instrument)
        result = register(command, owner_id=owner_id, token=uuid4().hex)
        position_id = result.position.id
        retry_key = uuid4().hex
        with pytest.raises(DataError):
            register(replace(command, quantity=Decimal("1e100")), owner_id=owner_id, token=retry_key)
        # Uma consulta nova após rollback prova que a sessão também ficou utilizável.
        position = db.session.get(model, position_id)
        assert position.quantity == Decimal("100")
        assert len(position.movements) == 1
        assert db.session.query(PositionSubmission).filter_by(owner_id=owner_id, token=retry_key).count() == 0
        assert db.session.query(Transaction).filter_by(owner_id=owner_id).one().quantity == Decimal("100")
        register(replace(command, quantity=Decimal("5")), owner_id=owner_id, token=retry_key)
        assert db.session.get(model, position_id).quantity == Decimal("105")


@pytest.mark.parametrize("instrument", ["stock", "option"])
def test_command_and_query_require_explicit_owner_without_request_context(review_case, instrument):
    app, data = review_case
    register = register_stock if instrument == "stock" else register_option
    query = stock_positions if instrument == "stock" else option_positions
    with app.app_context():
        owner_id = data["user_ids"][0]
        token = uuid4().hex
        with pytest.raises(ValueError, match="carteira"):
            register(_command(data, instrument, owner=2), owner_id=owner_id, token=token)
        assert not query(owner_id, data["portfolio_ids"][2])
        assert len(query(data["user_ids"][2], data["portfolio_ids"][2])) == 1
        result = register(_command(data, instrument), owner_id=owner_id, token=token)
        assert [position.id for position in query(owner_id)] == [result.position.id]
        assert not query(owner_id, currency="USD")


@pytest.mark.parametrize("instrument", ["stock", "option"])
def test_failure_during_closure_preserves_position_statement_and_transaction(review_case, instrument):
    app, data = review_case
    register = register_stock if instrument == "stock" else register_option
    close = close_stock if instrument == "stock" else close_option
    model = Position if instrument == "stock" else OptionPosition
    with app.app_context():
        owner_id = data["user_ids"][0]
        position_id = register(_command(data, instrument), owner_id=owner_id, token=uuid4().hex).position.id
        with pytest.raises(DataError):
            close(position_id, Decimal("1e100"), date.today(), Decimal("5"), owner_id=owner_id)
        position = db.session.get(model, position_id)
        assert position.quantity == Decimal("100")
        assert len(position.movements) == 1
        assert db.session.query(Transaction).filter_by(owner_id=owner_id).one().quantity == Decimal("100")
        close(position_id, Decimal("12"), date.today(), Decimal("5"), owner_id=owner_id)
        assert db.session.get(model, position_id).quantity == Decimal("95")


@pytest.mark.parametrize("instrument", ["stock", "option"])
def test_encerramento_rele_posicao_carregada_antes_de_outro_aporte(review_case, instrument):
    app, data = review_case
    register = register_stock if instrument == "stock" else register_option
    close = close_stock if instrument == "stock" else close_option
    model = Position if instrument == "stock" else OptionPosition
    owner_id = data["user_ids"][0]
    command = _command(data, instrument)
    with app.app_context():
        position_id = register(command, owner_id=owner_id, token=uuid4().hex).position.id
        carregada = db.session.get(model, position_id)
        assert carregada.quantity == Decimal("100")
        assert len(carregada.movements) == 1
        with app.app_context():
            register(replace(command, quantity=Decimal("50")), owner_id=owner_id, token=uuid4().hex)
        close(position_id, Decimal("12"), date.today(), Decimal("40"), owner_id=owner_id)
        db.session.expire_all()
        atual = db.session.get(model, position_id)
        assert atual.quantity == Decimal("110")
        assert len(atual.movements) == 3


@pytest.mark.parametrize("instrument", ["stock", "option"])
def test_aporte_rele_posicao_carregada_antes_de_outro_aporte(review_case, instrument):
    app, data = review_case
    register = register_stock if instrument == "stock" else register_option
    model = Position if instrument == "stock" else OptionPosition
    owner_id = data["user_ids"][0]
    command = _command(data, instrument)
    with app.app_context():
        position_id = register(command, owner_id=owner_id, token=uuid4().hex).position.id
        carregada = db.session.get(model, position_id)
        assert carregada.quantity == Decimal("100")
        with app.app_context():
            register(replace(command, quantity=Decimal("50")), owner_id=owner_id, token=uuid4().hex)
        register(replace(command, quantity=Decimal("25")), owner_id=owner_id, token=uuid4().hex)
        db.session.expire_all()
        assert db.session.get(model, position_id).quantity == Decimal("175")
