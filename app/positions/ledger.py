"""Preservação do extrato de uma posição que está sendo encerrada.

``app.positions.closure`` e ``app.options.closure`` apagam a posição
ao encerrá-la por inteiro, e o extrato vai junto em cascata. O relatório de
performance precisa preservar esses lançamentos para incluir posições
encerradas e evitar viés de sobrevivência.

Este módulo copia o que a série precisa para ``PositionLedgerArchive`` antes
da exclusão. Fica separado dos dois módulos de encerramento porque a cópia é
idêntica para ação e opção: só mudam a origem dos lançamentos e o rótulo do
instrumento.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from app import db
from app.models import (
    PositionLedgerArchive,
    PositionMovement,
    PositionMovementArchive,
    Side,
    Transaction,
)


def signed_quantity_direction(side: Side) -> Decimal:
    """``+1`` para posição comprada, ``-1`` para vendida — mesma convenção de
    sinal de ``app.routes.helpers.position_movement_events``, que é quem lê
    o resultado disto de volta."""
    return Decimal("1") if side == Side.BUY else Decimal("-1")


def archive_closed_position(
    *,
    instrument: str,
    position_id: int,
    ticker_id: int,
    portfolio_id: int,
    broker_id: int,
    owner_id: int,
    side: Side,
    entries: Sequence[tuple[date, Decimal]],
    closed_on: date,
) -> None:
    """Copia o extrato de uma posição para o arquivo, encerrando-o em zero.

    ``entries`` são pares ``(occurred_on, resulting_quantity)`` dos
    lançamentos da posição, na ordem em que aconteceram e com a quantidade
    SEM sinal, exatamente como ``PositionMovement.resulting_quantity`` a
    guarda. O sinal do lado é aplicado aqui, uma vez só.

    A linha final com quantidade zero em ``closed_on`` é o que faz a série
    parar de contar o ativo depois do encerramento; sem ela, a última
    quantidade conhecida valeria para sempre, e uma posição encerrada
    continuaria "aberta" no relatório para todo o futuro.

    Não faz ``commit``: quem inicia a operação de escrita é dono do limite
    transacional — mesma regra de ``app.quotes.history.upsert_quote_history``.
    """
    direction = signed_quantity_direction(side)
    for occurred_on, resulting_quantity in entries:
        db.session.add(
            PositionLedgerArchive(
                occurred_on=occurred_on,
                ticker_id=ticker_id,
                portfolio_id=portfolio_id,
                broker_id=broker_id,
                owner_id=owner_id,
                instrument=instrument,
                source_position_id=position_id,
                resulting_signed_quantity=direction * resulting_quantity,
            )
        )
    db.session.add(
        PositionLedgerArchive(
            occurred_on=closed_on,
            ticker_id=ticker_id,
            portfolio_id=portfolio_id,
            broker_id=broker_id,
            owner_id=owner_id,
            instrument=instrument,
            source_position_id=position_id,
            resulting_signed_quantity=Decimal("0"),
        )
    )


def archive_closed_stock_movements(
    *,
    position_id: int,
    ticker_id: int,
    portfolio_id: int,
    broker_id: int,
    owner_id: int,
    side: Side,
    movements: Sequence[PositionMovement],
    closing_transaction: Transaction,
) -> None:
    """Preserva integralmente os movimentos de ações antes da cascata.

    Os movimentos existentes são cópias fiéis. Uma linha ``close`` adicional
    deriva quantidade, preço, resultado e data da transação final, zera o saldo
    e não finge ser um ``PositionMovement`` de origem. O consumidor consegue
    distinguir o histórico novo do legado, para o qual esses fatos detalhados
    já não podem ser reconstruídos.
    """
    for movement in movements:
        db.session.add(
            PositionMovementArchive(
                owner_id=owner_id,
                ticker_id=ticker_id,
                portfolio_id=portfolio_id,
                broker_id=broker_id,
                side=side.value,
                source_position_id=position_id,
                source_movement_id=movement.id,
                kind=movement.kind.value,
                quantity_delta=movement.quantity_delta,
                price=movement.price,
                occurred_on=movement.occurred_on,
                result=movement.result,
                source_transaction_id=movement.transaction_id,
                resulting_quantity=movement.resulting_quantity,
                resulting_average_cost=movement.resulting_average_cost,
                source_created_at=movement.created_at,
            )
        )
    db.session.add(
        PositionMovementArchive(
            owner_id=owner_id,
            ticker_id=ticker_id,
            portfolio_id=portfolio_id,
            broker_id=broker_id,
            side=side.value,
            source_position_id=position_id,
            source_movement_id=None,
            kind="close",
            quantity_delta=-closing_transaction.quantity,
            price=closing_transaction.exit_price,
            occurred_on=closing_transaction.closed_on,
            result=closing_transaction.result,
            source_transaction_id=closing_transaction.id,
            resulting_quantity=Decimal("0"),
            resulting_average_cost=closing_transaction.average_cost,
            source_created_at=None,
        )
    )
