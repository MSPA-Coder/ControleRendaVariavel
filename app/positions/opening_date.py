"""Correção da data de abertura compartilhada por ações e opções."""

from datetime import date

from app import db
from app.models import OptionPosition, Position, PositionMovementKind, Transaction


def update_opening_date(position: Position | OptionPosition, new_date: date) -> None:
    if new_date == position.opened_on or not position.movements:
        return
    opening = next((m for m in position.movements if m.kind is PositionMovementKind.OPEN), None)
    if opening is None:
        raise ValueError("Não é possível corrigir a data sem uma abertura no extrato.")
    opening_key = (new_date, opening.id)
    if any(opening_key > (m.occurred_on, m.id) for m in position.movements if m is not opening):
        raise ValueError("A data inicial não pode ficar depois de outro movimento do extrato.")
    opening.occurred_on = new_date
    for movement in position.movements:
        if movement.kind is PositionMovementKind.DECREASE and movement.transaction_id is not None:
            transaction = db.session.get(Transaction, movement.transaction_id)
            if transaction is not None:
                transaction.opened_on = new_date
