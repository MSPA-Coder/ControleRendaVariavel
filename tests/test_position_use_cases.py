"""Comandos fora do HTTP continuam atômicos e isolados por dono.

Falha real de precisão no PostgreSQL ocorre depois de a chave e o aporte
serem iniciados. Nada pode sobreviver, nem impedir retry corrigido.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import DataError

from app import db
from app.models import OptionPosition, Position, PositionSubmission, Side, Transaction
from app.positions.commands import OptionPositionInput, PositionInput
from app.positions.queries import option_positions, stock_positions
from app.positions.use_cases import close_option, close_stock, register_option, register_stock
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
