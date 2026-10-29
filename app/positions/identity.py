"""Protege a identidade da exposição quando há encerramentos históricos."""

from sqlalchemy import select

from app import db
from app.models import (
    OptionPosition,
    Position,
    PositionMovementKind,
    Transaction,
    TransactionStatus,
)
from app.positions.commands import OptionPositionInput, PositionInput


def validate_identity_change(
    position: Position | OptionPosition, data: PositionInput | OptionPositionInput,
) -> None:
    if isinstance(position, Position) and isinstance(data, PositionInput):
        changed_instrument = position.ticker_id != data.ticker_id
        instrument_filter = Transaction.option_contract_id.is_(None)
    elif isinstance(position, OptionPosition) and isinstance(data, OptionPositionInput):
        changed_instrument = position.contract_id != data.contract_id
        instrument_filter = Transaction.option_contract_id.is_not(None)
    else:
        raise ValueError("Tipo de posição incompatível.")
    if not (changed_instrument or position.broker_id != data.broker_id
            or position.portfolio_id != data.portfolio_id or position.side != data.side):
        return
    has_decrease = any(m.kind is PositionMovementKind.DECREASE for m in position.movements)
    if not has_decrease:
        has_decrease = db.session.scalar(select(Transaction.id).where(
            Transaction.owner_id == position.owner_id,
            Transaction.source_position_id == position.id,
            Transaction.status == TransactionStatus.CLOSED,
            instrument_filter,
        ).limit(1)) is not None
    if has_decrease:
        raise ValueError(
            "Esta posição tem encerramentos parciais: não é possível alterar ativo ou contrato, "
            "corretora, carteira ou tipo. Corrija os lançamentos históricos separadamente."
        )
