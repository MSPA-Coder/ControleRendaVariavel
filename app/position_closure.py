from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app import db
from app.domain import operation_result
from app.models import Position, Transaction, TransactionStatus


def create_open_transaction_for_position(position: Position) -> Transaction:
    """Cria a linha ``status=OPEN`` que espelha uma posição recém-criada,
    para que ela apareça imediatamente em Transações como aberta."""

    transaction = Transaction(
        broker_id=position.broker_id,
        ticker_id=position.ticker_id,
        quantity=position.quantity,
        average_cost=position.average_cost,
        exit_price=None,
        side=position.side,
        opened_on=position.opened_on,
        closed_on=None,
        result_mode=position.result_mode,
        result=None,
        status=TransactionStatus.OPEN,
        position_kind=position.position_kind,
        source_position_id=position.id,
    )
    db.session.add(transaction)
    return transaction


def _open_transaction_for(position_id: int) -> Transaction | None:
    return db.session.scalar(
        select(Transaction).where(
            Transaction.source_position_id == position_id,
            Transaction.status == TransactionStatus.OPEN,
        )
    )


def sync_open_transaction_for_position(position: Position) -> None:
    """Mantém a linha aberta espelhada em dia após uma edição da posição.
    Cria a linha se, por algum motivo, ela ainda não existir (ex.: posições
    de antes desta migração que não tenham sido backfilled)."""

    transaction = _open_transaction_for(position.id)
    if transaction is None:
        create_open_transaction_for_position(position)
        return
    transaction.broker_id = position.broker_id
    transaction.ticker_id = position.ticker_id
    transaction.quantity = position.quantity
    transaction.average_cost = position.average_cost
    transaction.side = position.side
    transaction.opened_on = position.opened_on
    transaction.result_mode = position.result_mode
    transaction.position_kind = position.position_kind


def delete_open_transaction_for_position(position_id: int) -> None:
    """Remove a linha aberta espelhada quando a posição é excluída sem
    encerramento (ver ``routes.positions.delete_position``)."""

    transaction = _open_transaction_for(position_id)
    if transaction is not None:
        db.session.delete(transaction)


def close_open_position(
    position_id: int, exit_price: Decimal, closed_on: date
) -> Transaction | None:
    """Atomically close an open position, returning ``None`` when it is gone.

    Reaproveita a linha ``status=OPEN`` já existente em ``transactions``
    (criada quando a posição foi cadastrada) e a atualiza para
    ``status=CLOSED``, em vez de inserir uma nova linha — evita duplicar
    ``source_position_id`` (único) e preserva o mesmo registro ao longo do
    ciclo de vida da posição.
    """

    position = db.session.scalar(
        select(Position).where(Position.id == position_id).with_for_update()
    )
    if position is None:
        return None
    if closed_on < position.opened_on:
        db.session.rollback()
        raise ValueError("Closing date cannot precede the opening date.")

    result = operation_result(
        position.side.value,
        position.quantity,
        position.average_cost,
        exit_price,
        position.result_mode,
    )
    transaction = _open_transaction_for(position.id)
    if transaction is None:
        transaction = Transaction(source_position_id=position.id)
        db.session.add(transaction)
    transaction.broker_id = position.broker_id
    transaction.ticker_id = position.ticker_id
    transaction.quantity = position.quantity
    transaction.average_cost = position.average_cost
    transaction.exit_price = exit_price
    transaction.side = position.side
    transaction.opened_on = position.opened_on
    transaction.closed_on = closed_on
    transaction.result_mode = position.result_mode
    transaction.result = result
    transaction.status = TransactionStatus.CLOSED
    transaction.position_kind = position.position_kind
    transaction.notes = f"Encerrada a partir da posi\u00e7\u00e3o #{position.id}."
    db.session.delete(position)
    db.session.commit()
    return transaction
