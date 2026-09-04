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
from app.models import PositionLedgerArchive, Side


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
    transacional — mesma regra de ``app.routes.helpers.upsert_quote_history``.
    """
    direction = signed_quantity_direction(side)
    for occurred_on, resulting_quantity in entries:
        db.session.add(
            PositionLedgerArchive(
                occurred_on=occurred_on,
                ticker_id=ticker_id,
                portfolio_id=portfolio_id,
                broker_id=broker_id,
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
            instrument=instrument,
            source_position_id=position_id,
            resulting_signed_quantity=Decimal("0"),
        )
    )
