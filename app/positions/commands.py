"""Entradas tipadas dos casos de uso; não dependem de formulário ou sessão HTTP."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models import Side


@dataclass(frozen=True, slots=True)
class PositionInput:
    broker_id: int
    ticker_id: int
    quantity: Decimal
    average_cost: Decimal
    side: Side
    opened_on: date
    target_multiplier: Decimal
    portfolio_id: int


@dataclass(frozen=True, slots=True)
class OptionPositionInput:
    broker_id: int
    contract_id: int
    quantity: Decimal
    average_cost: Decimal
    target_price: Decimal | None
    side: Side
    opened_on: date
    portfolio_id: int
