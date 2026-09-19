"""Consultas somente-leitura usadas pelo contrato de patrimônio v2.

A integração não tem sessão, mas isso não elimina autorização: cada consulta
recebe o ``owner_id`` explicitamente resolvido pela configuração do publicador.
O token autoriza a integração; o owner limita o conjunto financeiro publicado.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app import db
from app.models import (
    Dividend,
    OptionContract,
    OptionPosition,
    OptionPositionMovement,
    Portfolio,
    Position,
    PositionLedgerArchive,
    PositionMovement,
    QuoteHistory,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
)
from app.positions.holdings_history import HoldingEvent


def portfolios(owner_id: int) -> list[Portfolio]:
    return list(
        db.session.scalars(
            select(Portfolio)
            .where(Portfolio.owner_id == owner_id)
            .order_by(Portfolio.simulated, Portfolio.name, Portfolio.id)
        )
    )


def real_positions(owner_id: int) -> list[Position]:
    statement = (
        select(Position)
        .join(Position.portfolio_ref)
        .where(Position.owner_id == owner_id, Portfolio.simulated.is_(False))
        .options(
            joinedload(Position.quote),
            joinedload(Position.broker_ref),
            joinedload(Position.ticker_ref),
            joinedload(Position.portfolio_ref),
            joinedload(Position.movements),
        )
        .order_by(Position.id)
    )
    return list(db.session.scalars(statement).unique())


def quote_series(
    ticker_ids: Iterable[int], *, start: date, end: date
) -> dict[int, list[tuple[date, Decimal]]]:
    ids = sorted(set(ticker_ids))
    result: dict[int, list[tuple[date, Decimal]]] = {ticker_id: [] for ticker_id in ids}
    if not ids:
        return result
    rows = db.session.execute(
        select(QuoteHistory.ticker_id, QuoteHistory.recorded_date, QuoteHistory.price)
        .where(
            QuoteHistory.ticker_id.in_(ids),
            QuoteHistory.recorded_date >= start,
            QuoteHistory.recorded_date <= end,
        )
        .order_by(QuoteHistory.ticker_id, QuoteHistory.recorded_date)
    )
    for ticker_id, recorded_date, price in rows:
        result[ticker_id].append((recorded_date, price))
    return result


def dividends(desde: date, ate: date, owner_id: int) -> list[Dividend]:
    statement = (
        select(Dividend)
        .where(
            Dividend.owner_id == owner_id,
            Dividend.payment_date >= desde,
            Dividend.payment_date <= ate,
        )
        .options(joinedload(Dividend.broker_ref), joinedload(Dividend.ticker_ref))
        .order_by(Dividend.payment_date, Dividend.id)
    )
    return list(db.session.scalars(statement))


def closed_transactions(desde: date, ate: date, owner_id: int) -> list[Transaction]:
    statement = (
        select(Transaction)
        .join(Transaction.portfolio_ref)
        .where(
            Portfolio.simulated.is_(False),
            Transaction.owner_id == owner_id,
            Transaction.status == TransactionStatus.CLOSED,
            Transaction.closed_on >= desde,
            Transaction.closed_on <= ate,
        )
        .options(
            joinedload(Transaction.broker_ref),
            joinedload(Transaction.ticker_ref),
            joinedload(Transaction.option_contract_ref).joinedload(OptionContract.ticker_ref),
            joinedload(Transaction.portfolio_ref),
        )
        .order_by(Transaction.closed_on, Transaction.id)
    )
    return list(db.session.scalars(statement).unique())


def performance_events(reference: date, owner_id: int) -> list[HoldingEvent]:
    """Lê o extrato do owner publicado até a data de referência."""
    stock = (
        select(
            func.coalesce(PositionMovement.occurred_on, Position.opened_on),
            Position.id,
            Position.ticker_id,
            Position.side,
            func.coalesce(PositionMovement.resulting_quantity, Position.quantity),
        )
        .select_from(Position)
        .join(Position.portfolio_ref)
        .outerjoin(PositionMovement, PositionMovement.position_id == Position.id)
        .where(
            Position.owner_id == owner_id,
            Portfolio.simulated.is_(False),
            func.coalesce(PositionMovement.occurred_on, Position.opened_on) <= reference,
        )
        .order_by(PositionMovement.occurred_on, PositionMovement.id, Position.id)
    )
    option = (
        select(
            func.coalesce(OptionPositionMovement.occurred_on, OptionPosition.opened_on),
            OptionPosition.id,
            OptionContract.ticker_id,
            OptionPosition.side,
            func.coalesce(OptionPositionMovement.resulting_quantity, OptionPosition.quantity),
        )
        .select_from(OptionPosition)
        .join(OptionPosition.contract)
        .join(OptionPosition.portfolio_ref)
        .outerjoin(
            OptionPositionMovement,
            OptionPositionMovement.option_position_id == OptionPosition.id,
        )
        .where(
            OptionPosition.owner_id == owner_id,
            Portfolio.simulated.is_(False),
            func.coalesce(OptionPositionMovement.occurred_on, OptionPosition.opened_on)
            <= reference,
        )
        .order_by(OptionPositionMovement.occurred_on, OptionPositionMovement.id, OptionPosition.id)
    )
    events: list[HoldingEvent] = []
    for occurred_on, position_id, ticker_id, side, quantity in db.session.execute(stock):
        sign = Decimal("1") if side == Side.BUY else Decimal("-1")
        events.append(HoldingEvent(occurred_on, ticker_id, sign * quantity, ("stock", position_id)))
    for occurred_on, position_id, ticker_id, side, quantity in db.session.execute(option):
        sign = Decimal("1") if side == Side.BUY else Decimal("-1")
        events.append(HoldingEvent(occurred_on, ticker_id, sign * quantity, ("option", position_id)))

    archive = (
        select(
            PositionLedgerArchive.occurred_on,
            PositionLedgerArchive.ticker_id,
            PositionLedgerArchive.instrument,
            PositionLedgerArchive.source_position_id,
            PositionLedgerArchive.resulting_signed_quantity,
        )
        .join(Portfolio, Portfolio.id == PositionLedgerArchive.portfolio_id)
        .where(
            PositionLedgerArchive.owner_id == owner_id,
            Portfolio.simulated.is_(False),
            PositionLedgerArchive.occurred_on <= reference,
        )
        .order_by(PositionLedgerArchive.occurred_on, PositionLedgerArchive.id)
    )
    for occurred_on, ticker_id, instrument, position_id, quantity in db.session.execute(archive):
        events.append(HoldingEvent(occurred_on, ticker_id, quantity, (instrument, position_id)))
    events.sort(key=lambda event: event.occurred_on)
    return events


def tickers(ticker_ids: Iterable[int]) -> dict[int, Ticker]:
    ids = sorted(set(ticker_ids))
    if not ids:
        return {}
    return {ticker.id: ticker for ticker in db.session.scalars(select(Ticker).where(Ticker.id.in_(ids)))}
