from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app import db
from app.domain import operation_result
from app.models import Position, Transaction


def close_open_position(
    position_id: int, exit_price: Decimal, closed_on: date
) -> Transaction | None:
    """Atomically close an open position, returning ``None`` when it is gone."""

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
    transaction = Transaction(
        broker_id=position.broker_id,
        ticker_id=position.ticker_id,
        quantity=position.quantity,
        average_cost=position.average_cost,
        exit_price=exit_price,
        side=position.side,
        opened_on=position.opened_on,
        closed_on=closed_on,
        result_mode=position.result_mode,
        result=result,
        position_kind=position.position_kind,
        source_position_id=position.id,
        notes=f"Encerrada a partir da posi\u00e7\u00e3o #{position.id}.",
    )
    db.session.add(transaction)
    db.session.delete(position)
    db.session.commit()
    return transaction
