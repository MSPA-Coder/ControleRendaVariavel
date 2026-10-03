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
    Broker,
    OptionContract,
    OptionPosition,
    OptionPositionMovement,
    Portfolio,
    Position,
    PositionLedgerArchive,
    PositionMovement,
    Side,
)
from app.positions.holdings_history import HoldingEvent


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



def position_timeline(
    owner_id: int,
    *,
    reference: date | None = None,
    portfolio_id: int | None = None,
    broker: str | None = None,
    include_options: bool = True,
    ticker_ids: Iterable[int] | None = None,
) -> list[HoldingEvent]:
    """A linha do tempo de posições REAIS do dono: ações, opções e as já encerradas.

    É a ÚNICA leitura do extrato de posições. O TWR da tela
    (``app.routes.helpers.position_movement_events``, com o dono da requisição) e o
    contrato v4 (``performance_events``, com o dono da configuração do publicador)
    passam por aqui; antes eram duas cópias de cerca de 270 linhas, com diferenças
    na abertura sintética de ação sem extrato que ninguém tinha decidido.

    São até três consultas -- ações vivas, opções vivas e o arquivo das encerradas
    (``PositionLedgerArchive``) --, nunca uma por posição. O arquivo entra porque
    encerrar uma posição apaga o extrato dela em cascata: sem ele, o relatório mediria
    apenas os ativos que continuaram na carteira (viés de sobrevivência).

    - ``reference``: só eventos até essa data (inclusive); ``None`` não corta.
    - ``portfolio_id`` e ``broker`` (pelo nome): os filtros da tela de performance.
    - ``include_options=False`` lê só ações; ``ticker_ids`` limita o histórico aos
      ativos que o chamador precisa (lista vazia devolve vazio).

    A carteira simulada fica de fora sempre, de forma explícita: posição simulada já
    não gera movimento, mas o relatório financeiro não deve depender disso.

    O sinal vem do ``side`` da POSIÇÃO, que não existe no movimento, e é aplicado a
    ``resulting_quantity``; os quatro tipos de movimento são lidos pela mesma fórmula,
    sem examinar ``kind``, porque o saldo resultante já está gravado em cada linha.

    Posição viva SEM nenhuma linha de extrato (ação ou opção) entra como uma única
    abertura sintética, na data ``opened_on`` e com a quantidade consolidada. O
    ``outer join`` faz isso no mesmo snapshot que lê os movimentos: uma posição com
    extrato nunca recebe uma segunda linha sintética, e não há uma segunda consulta
    vulnerável a uma inserção concorrente. Na produção só opções legadas caem aqui; a
    ação sem extrato não existe hoje, e dar a ela o mesmo tratamento mantém as duas
    pontas iguais (o v4 já fazia; a tela não).

    O ``price`` do movimento NÃO é lido de propósito: o fluxo do TWR é avaliado a preço
    de mercado da data, não ao preço lançado (ver ``portfolio_flow_series``). O ticker
    de uma opção é o do CONTRATO, nunca o do ativo-objeto. ``position_key`` carrega a
    origem (``("stock", id)`` / ``("option", id)``) porque ``Position`` e
    ``OptionPosition`` têm sequências de id independentes.
    """
    ticker_filter = list(ticker_ids) if ticker_ids is not None else None
    if ticker_filter == []:
        return []

    stock_date = func.coalesce(PositionMovement.occurred_on, Position.opened_on)
    stock = (
        select(
            stock_date,
            Position.id,
            Position.ticker_id,
            Position.side,
            func.coalesce(PositionMovement.resulting_quantity, Position.quantity),
        )
        .select_from(Position)
        .join(Position.portfolio_ref)
        .outerjoin(PositionMovement, PositionMovement.position_id == Position.id)
        .where(Position.owner_id == owner_id, Portfolio.simulated.is_(False))
        .order_by(stock_date, PositionMovement.id, Position.id)
    )
    if reference is not None:
        stock = stock.where(stock_date <= reference)
    if portfolio_id is not None:
        stock = stock.where(Position.portfolio_id == portfolio_id)
    if broker:
        stock = stock.join(Position.broker_ref).where(Broker.name == broker)
    if ticker_filter is not None:
        stock = stock.where(Position.ticker_id.in_(ticker_filter))

    events: list[HoldingEvent] = []
    for occurred_on, position_id, ticker_id, side, quantity in db.session.execute(stock):
        sign = Decimal("1") if side == Side.BUY else Decimal("-1")
        events.append(HoldingEvent(occurred_on, ticker_id, sign * quantity, ("stock", position_id)))

    if include_options:
        option_date = func.coalesce(OptionPositionMovement.occurred_on, OptionPosition.opened_on)
        option = (
            select(
                option_date,
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
            .where(OptionPosition.owner_id == owner_id, Portfolio.simulated.is_(False))
            .order_by(option_date, OptionPositionMovement.id, OptionPosition.id)
        )
        if reference is not None:
            option = option.where(option_date <= reference)
        if portfolio_id is not None:
            option = option.where(OptionPosition.portfolio_id == portfolio_id)
        if broker:
            option = option.join(OptionPosition.broker_ref).where(Broker.name == broker)
        if ticker_filter is not None:
            option = option.where(OptionContract.ticker_id.in_(ticker_filter))
        for occurred_on, position_id, ticker_id, side, quantity in db.session.execute(option):
            sign = Decimal("1") if side == Side.BUY else Decimal("-1")
            events.append(HoldingEvent(occurred_on, ticker_id, sign * quantity, ("option", position_id)))

    # Posições já encerradas não têm mais extrato (a exclusão o leva em cascata); o
    # que sobrou delas está no arquivo. O sinal já foi aplicado na gravação.
    archive = (
        select(
            PositionLedgerArchive.occurred_on,
            PositionLedgerArchive.ticker_id,
            PositionLedgerArchive.instrument,
            PositionLedgerArchive.source_position_id,
            PositionLedgerArchive.resulting_signed_quantity,
        )
        .join(Portfolio, Portfolio.id == PositionLedgerArchive.portfolio_id)
        .where(PositionLedgerArchive.owner_id == owner_id, Portfolio.simulated.is_(False))
        .order_by(PositionLedgerArchive.occurred_on, PositionLedgerArchive.id)
    )
    if reference is not None:
        archive = archive.where(PositionLedgerArchive.occurred_on <= reference)
    if not include_options:
        archive = archive.where(PositionLedgerArchive.instrument == "stock")
    if portfolio_id is not None:
        archive = archive.where(PositionLedgerArchive.portfolio_id == portfolio_id)
    if broker:
        archive = archive.join(Broker, Broker.id == PositionLedgerArchive.broker_id).where(Broker.name == broker)
    if ticker_filter is not None:
        archive = archive.where(PositionLedgerArchive.ticker_id.in_(ticker_filter))
    for occurred_on, ticker_id, instrument, position_id, quantity in db.session.execute(archive):
        events.append(HoldingEvent(occurred_on, ticker_id, quantity, (instrument, position_id)))

    events.sort(key=lambda event: event.occurred_on)
    return events


def performance_events(reference: date, owner_id: int) -> list[HoldingEvent]:
    """O extrato do dono publicado até a data de referência (contrato v4)."""
    return position_timeline(owner_id, reference=reference)
