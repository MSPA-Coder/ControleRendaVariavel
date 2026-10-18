"""Histórico de acesso a instrumentos, atualizado na transação financeira."""

from datetime import date

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app import db
from app.models import UserTickerEntitlement


def grant_ticker_entitlement(*, user_id: int, ticker_id: int, held_on: date) -> None:
    """Registra a primeira posse confirmada sem revogar histórico ao encerrar."""
    statement = insert(UserTickerEntitlement).values(
        user_id=user_id, ticker_id=ticker_id, first_held_on=held_on
    )
    db.session.execute(
        statement.on_conflict_do_update(
            index_elements=[UserTickerEntitlement.user_id, UserTickerEntitlement.ticker_id],
            set_={"first_held_on": func.least(UserTickerEntitlement.first_held_on, statement.excluded.first_held_on)},
        )
    )
