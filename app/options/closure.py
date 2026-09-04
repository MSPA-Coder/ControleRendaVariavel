"""Ciclo de vida de uma posição de opções: abertura, aumento, ajuste e
encerramento (total ou parcial).

Espelha ``app.positions.closure`` (ações) trocando ``Position`` por
``OptionPosition``, ``PositionMovement`` por ``OptionPositionMovement`` e
``ticker_id`` por ``contract_id``. O algoritmo em si (custo médio ponderado,
replay do extrato e divisão de quantidade num encerramento parcial) é o mesmo
dos dois instrumentos e vive em ``app.core.domain``; só a persistência muda entre
os dois módulos.

Três registros andam juntos e são mantidos em dia aqui, nunca nas rotas:

- ``OptionPosition`` — o estado atual consolidado (uma linha por contrato,
  corretora, tipo e carteira);
- ``OptionPositionMovement`` — o extrato que explica como se chegou a esse
  estado;
- ``Transaction`` — o que aparece na aba Transações: uma linha aberta
  espelhando a posição (``option_contract_id`` preenchido, ``ticker_id``
  nulo) e uma linha fechada para cada parcela realizada.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, select

from app import db
from app.core.domain import (
    StatementEntry,
    is_duplicate_entry,
    plan_position_closure,
    replay_statement,
    weighted_average_cost,
)
from app.models import (
    OptionPosition,
    OptionPositionMovement,
    Portfolio,
    PositionMovementKind,
    Transaction,
    TransactionStatus,
)
from app.positions.ledger import archive_closed_position


def _is_simulated(portfolio_id: int) -> bool:
    """Mesma ideia de ``position_closure._is_simulated``, para opções: consulta
    direta porque o ``candidate`` de ``create_or_merge_position`` ainda não foi
    adicionado à sessão no ponto em que isso precisa ser decidido."""

    portfolio = db.session.get(Portfolio, portfolio_id)
    return portfolio is not None and portfolio.simulated


SIMULATED_MERGE_REJECTED = (
    "A carteira Simulada não permite reforçar uma posição existente — isso "
    "seria um aumento, e a carteira Simulada só permite criar, editar e "
    "excluir. Edite a posição já cadastrada em vez de lançar uma nova "
    "entrada."
)

SIMULATED_CLOSE_REJECTED = (
    "A carteira Simulada não permite encerramento total nem parcial. Para "
    "desfazer a posição, exclua-a."
)


def create_open_transaction_for_position(position: OptionPosition) -> Transaction:
    """Cria a linha ``status=OPEN`` que espelha uma posição de opção recém-
    criada, para que ela apareça imediatamente em Transações como aberta."""

    transaction = Transaction(
        broker_id=position.broker_id,
        option_contract_id=position.contract_id,
        quantity=position.quantity,
        average_cost=position.average_cost,
        exit_price=None,
        side=position.side,
        opened_on=position.opened_on,
        closed_on=None,
        result_mode=position.result_mode,
        result=None,
        status=TransactionStatus.OPEN,
        portfolio_id=position.portfolio_id,
        source_position_id=position.id,
    )
    db.session.add(transaction)
    return transaction


def _open_transaction_for(option_position_id: int) -> Transaction | None:
    return db.session.scalar(
        select(Transaction).where(
            Transaction.source_position_id == option_position_id,
            Transaction.status == TransactionStatus.OPEN,
            # ``Position`` e ``OptionPosition`` têm sequências de id
            # independentes: sem este filtro, uma posição de ação com o
            # mesmo id numérico faria esta consulta encontrar duas linhas
            # ou a linha errada.
            Transaction.option_contract_id.is_not(None),
        )
    )


def sync_open_transaction_for_position(position: OptionPosition) -> None:
    """Mantém a linha aberta espelhada em dia após uma edição da posição.
    Cria a linha se, por algum motivo, ela ainda não existir.

    Posição simulada não tem transação: não faz nada (ver
    ``position_closure.sync_open_transaction_for_position``, o mesmo
    mecanismo para ações, inclusive para a transição simulada -> real: a linha
    "nasce" aqui porque nenhuma existia antes)."""

    if position.simulated:
        return
    transaction = _open_transaction_for(position.id)
    if transaction is None:
        create_open_transaction_for_position(position)
        return
    transaction.broker_id = position.broker_id
    transaction.option_contract_id = position.contract_id
    transaction.quantity = position.quantity
    transaction.average_cost = position.average_cost
    transaction.side = position.side
    transaction.opened_on = position.opened_on
    transaction.result_mode = position.result_mode
    transaction.portfolio_id = position.portfolio_id


def delete_open_transaction_for_position(position_id: int) -> None:
    """Remove a linha aberta espelhada quando a posição é excluída sem
    encerramento (ver ``routes.options.delete_position``)."""

    transaction = _open_transaction_for(position_id)
    if transaction is not None:
        db.session.delete(transaction)


def record_movement(
    position: OptionPosition,
    kind: PositionMovementKind,
    *,
    quantity_delta: Decimal,
    price: Decimal,
    occurred_on: date,
    result: Decimal | None = None,
    transaction_id: int | None = None,
) -> OptionPositionMovement:
    """Acrescenta um lançamento ao extrato da posição de opção.

    O estado resultante é fotografado a partir da posição, que já deve estar
    atualizada quando esta função é chamada: é o custo médio vigente
    **depois** do movimento que interessa a quem lê o extrato.
    """

    movement = OptionPositionMovement(
        option_position_id=position.id,
        kind=kind,
        quantity_delta=quantity_delta,
        price=price,
        occurred_on=occurred_on,
        result=result,
        transaction_id=transaction_id,
        resulting_quantity=position.quantity,
        resulting_average_cost=position.average_cost,
    )
    db.session.add(movement)
    return movement


def replay_movements(position: OptionPosition) -> None:
    """Recalcula o saldo de cada movimento e o estado atual da posição.

    Necessário quando um movimento sai do meio do extrato — ao desfazer um
    encerramento parcial. O cálculo em si é ``domain.replay_statement``; esta
    função só traduz de/para os modelos ORM (ver
    ``position_closure.replay_movements``, o mesmo algoritmo para ações).
    """

    movements = list(position.movements)
    replayed = replay_statement(
        [
            StatementEntry(
                kind=movement.kind, quantity_delta=movement.quantity_delta, price=movement.price
            )
            for movement in movements
        ]
    )
    if replayed is None:
        # Sem um lançamento de abertura não há de onde partir; preserva o que
        # está gravado em vez de zerar a posição.
        return
    for movement, result in zip(movements, replayed, strict=True):
        movement.resulting_quantity = result.resulting_quantity
        movement.resulting_average_cost = result.resulting_average_cost
    position.quantity = replayed[-1].resulting_quantity
    position.average_cost = replayed[-1].resulting_average_cost


def partial_close_of_open_position(transaction: Transaction) -> OptionPosition | None:
    """A posição de opção ainda aberta que esta transação encerrou
    parcialmente.

    ``None`` quando a transação não veio de uma posição de opção, ainda está
    aberta ou encerrou a posição por inteiro — nesse caso ela é um registro
    histórico independente, e não o espelho de um movimento de uma posição
    viva.
    """

    if (
        transaction.status is not TransactionStatus.CLOSED
        or transaction.source_position_id is None
        or transaction.option_contract_id is None
    ):
        return None
    return db.session.get(OptionPosition, transaction.source_position_id)


def revert_partial_close(transaction: Transaction) -> OptionPosition | None:
    """Desfaz o encerramento parcial que gerou esta transação.

    Devolve a quantidade encerrada à posição e remove a baixa do extrato, para
    que excluir a transação em Transações realmente volte ao estado anterior.

    Não faz ``commit``: quem exclui a transação é dono do limite transacional.
    """

    position = db.session.scalar(
        select(OptionPosition)
        .where(OptionPosition.id == transaction.source_position_id)
        .with_for_update()
    )
    if position is None:
        return None
    movement = db.session.scalar(
        select(OptionPositionMovement).where(
            OptionPositionMovement.transaction_id == transaction.id
        )
    )
    if movement is None:
        # Encerramento parcial anterior ao vínculo entre baixa e transação:
        # devolve a quantidade sem reaplicar a cadeia, que não tem como ser
        # identificada com segurança.
        position.quantity = position.quantity + transaction.quantity
    else:
        position.movements.remove(movement)
        replay_movements(position)
    sync_open_transaction_for_position(position)
    return position


def _mergeable_statement(candidate: OptionPosition) -> Select[tuple[OptionPosition]]:
    """Consulta da posição de opção aberta que um novo aporte reforçaria.

    Unifica somente o que representa a mesma exposição: mesmo **contrato**
    (não apenas o mesmo ativo-objeto — strike e vencimento diferentes nunca
    se fundem), mesma corretora, mesmo tipo (compra ou venda) e mesma
    carteira. É o análogo de ``position_closure._mergeable_statement``
    trocando ticker por contrato.
    """

    return (
        select(OptionPosition)
        .where(
            OptionPosition.broker_id == candidate.broker_id,
            OptionPosition.contract_id == candidate.contract_id,
            OptionPosition.side == candidate.side,
            OptionPosition.portfolio_id == candidate.portfolio_id,
        )
        .order_by(OptionPosition.id)
        .limit(1)
    )


def _mergeable_position(candidate: OptionPosition) -> OptionPosition | None:
    """A posição que o aporte vai reforçar, já travada para escrita."""

    return db.session.scalar(_mergeable_statement(candidate).with_for_update())


def duplicate_entry(candidate: OptionPosition) -> OptionPositionMovement | None:
    """O último lançamento da posição, quando ele é idêntico a este aporte.

    Serve à confirmação extra no cadastro (ver
    ``position_closure.duplicate_entry``, o mesmo mecanismo para ações).
    """

    existing = db.session.scalar(_mergeable_statement(candidate))
    if existing is None or not existing.movements:
        return None
    last = existing.movements[-1]
    if is_duplicate_entry(
        last_kind=last.kind,
        last_quantity_delta=last.quantity_delta,
        last_price=last.price,
        last_occurred_on=last.occurred_on,
        candidate_quantity=candidate.quantity,
        candidate_price=candidate.average_cost,
        candidate_occurred_on=candidate.opened_on,
    ):
        return last
    return None


def create_or_merge_position(candidate: OptionPosition) -> tuple[OptionPosition, bool]:
    """Registra um aporte, abrindo uma posição de opção nova ou reforçando a
    existente.

    Devolve a posição persistida e se ela já existia. Quando já existia, o
    ``candidate`` é descartado: a quantidade é somada e o custo médio passa a
    ser a média ponderada dos dois (ver ``domain.weighted_average_cost``). A
    data inicial recua para a mais antiga das duas, e o target da posição
    existente é preservado, já que um aporte não é motivo para redefini-lo.

    Não faz ``commit``: quem inicia a operação de escrita é dono do limite
    transacional.
    """

    existing = _mergeable_position(candidate)
    if existing is not None and _is_simulated(candidate.portfolio_id):
        # A carteira Simulada não tem "aumento": uma
        # segunda entrada para a mesma exposição é recusada, nunca fundida.
        raise ValueError(SIMULATED_MERGE_REJECTED)
    if existing is None:
        db.session.add(candidate)
        db.session.flush()
        if not candidate.simulated:
            record_movement(
                candidate,
                PositionMovementKind.OPEN,
                quantity_delta=candidate.quantity,
                price=candidate.average_cost,
                occurred_on=candidate.opened_on,
            )
            create_open_transaction_for_position(candidate)
        return candidate, False

    existing.average_cost = weighted_average_cost(
        existing.quantity,
        existing.average_cost,
        candidate.quantity,
        candidate.average_cost,
    )
    existing.quantity = existing.quantity + candidate.quantity
    existing.opened_on = min(existing.opened_on, candidate.opened_on)
    record_movement(
        existing,
        PositionMovementKind.INCREASE,
        quantity_delta=candidate.quantity,
        price=candidate.average_cost,
        occurred_on=candidate.opened_on,
    )
    sync_open_transaction_for_position(existing)
    return existing, True


def record_position_adjustment(
    position: OptionPosition,
    previous_quantity: Decimal,
    previous_average_cost: Decimal,
) -> None:
    """Mantém o extrato coerente depois de uma edição direta da posição (ver
    ``position_closure.record_position_adjustment``, o mesmo mecanismo para
    ações).

    Posição simulada não tem extrato: não faz nada. O descarte do extrato ao
    entrar na Simulada é responsabilidade de
    ``discard_simulation_history``, chamada à parte pela rota."""

    if position.simulated:
        return
    movements = position.movements
    opening = movements[0] if movements else None
    if opening is None:
        record_movement(
            position,
            PositionMovementKind.OPEN,
            quantity_delta=position.quantity,
            price=position.average_cost,
            occurred_on=position.opened_on,
        )
        return
    if len(movements) == 1 and opening.kind is PositionMovementKind.OPEN:
        opening.quantity_delta = position.quantity
        opening.price = position.average_cost
        opening.occurred_on = position.opened_on
        opening.resulting_quantity = position.quantity
        opening.resulting_average_cost = position.average_cost
        return
    if (
        position.quantity == previous_quantity
        and position.average_cost == previous_average_cost
    ):
        return
    record_movement(
        position,
        PositionMovementKind.ADJUSTMENT,
        quantity_delta=position.quantity - previous_quantity,
        price=position.average_cost,
        occurred_on=date.today(),
    )


def discard_simulation_history(position: OptionPosition) -> None:
    """Apaga a linha aberta e o extrato ao entrar na carteira Simulada."""

    delete_open_transaction_for_position(position.id)
    for movement in list(position.movements):
        position.movements.remove(movement)


def close_open_position(
    position_id: int,
    exit_price: Decimal,
    closed_on: date,
    quantity: Decimal | None = None,
) -> Transaction | None:
    """Encerra uma posição de opção aberta, por inteiro ou em parte, de forma
    atômica. Devolve ``None`` quando a posição não existe mais.

    Mesma mecânica de ``position_closure.close_open_position``: reaproveita a
    linha ``status=OPEN`` já existente em ``transactions`` num encerramento
    total, e insere uma linha fechada nova + reduz o saldo num parcial. A
    carteira Simulada não permite encerramento — ver a mesma
    guarda em ``position_closure.close_open_position``.
    """

    position = db.session.scalar(
        select(OptionPosition).where(OptionPosition.id == position_id).with_for_update()
    )
    if position is None:
        return None
    if position.simulated:
        db.session.rollback()
        raise ValueError(SIMULATED_CLOSE_REJECTED)
    try:
        split = plan_position_closure(
            held_quantity=position.quantity,
            average_cost=position.average_cost,
            side=position.side.value,
            result_mode=position.result_mode,
            opened_on=position.opened_on,
            closed_on=closed_on,
            exit_price=exit_price,
            requested_quantity=quantity,
        )
    except ValueError:
        db.session.rollback()
        raise

    if split.is_total:
        transaction = _close_entirely(position, exit_price, closed_on, split.result)
    else:
        transaction = _close_partially(
            position, split.closing_quantity, exit_price, closed_on, split.result
        )
    db.session.commit()
    return transaction


def _close_entirely(
    position: OptionPosition, exit_price: Decimal, closed_on: date, result: Decimal
) -> Transaction:
    transaction = _open_transaction_for(position.id)
    if transaction is None:
        transaction = Transaction(source_position_id=position.id)
        db.session.add(transaction)
    transaction.broker_id = position.broker_id
    transaction.option_contract_id = position.contract_id
    transaction.quantity = position.quantity
    transaction.average_cost = position.average_cost
    transaction.exit_price = exit_price
    transaction.side = position.side
    transaction.opened_on = position.opened_on
    transaction.closed_on = closed_on
    transaction.result_mode = position.result_mode
    transaction.result = result
    transaction.status = TransactionStatus.CLOSED
    transaction.portfolio_id = position.portfolio_id
    transaction.notes = f"Encerrada a partir da posição de opção #{position.id}."
    # Mesma preservação da posição de ações (ver `app.positions.ledger`). O
    # ticker é o do CONTRATO, nunca o do ativo-objeto: é ele que tem preço e
    # é o que a série de performance soma.
    archive_closed_position(
        instrument="option",
        position_id=position.id,
        ticker_id=position.contract.ticker_id,
        portfolio_id=position.portfolio_id,
        broker_id=position.broker_id,
        side=position.side,
        entries=[
            (movement.occurred_on, movement.resulting_quantity)
            for movement in position.movements
        ],
        closed_on=closed_on,
    )
    db.session.delete(position)
    return transaction


def _close_partially(
    position: OptionPosition,
    closing_quantity: Decimal,
    exit_price: Decimal,
    closed_on: date,
    result: Decimal,
) -> Transaction:
    transaction = Transaction(
        broker_id=position.broker_id,
        option_contract_id=position.contract_id,
        quantity=closing_quantity,
        average_cost=position.average_cost,
        exit_price=exit_price,
        side=position.side,
        opened_on=position.opened_on,
        closed_on=closed_on,
        result_mode=position.result_mode,
        result=result,
        status=TransactionStatus.CLOSED,
        portfolio_id=position.portfolio_id,
        source_position_id=position.id,
        notes=f"Encerramento parcial da posição de opção #{position.id}.",
    )
    db.session.add(transaction)
    # A baixa guarda o id da transação, e por isso precisa dele antes do
    # commit: é esse vínculo que permite desfazer a operação depois.
    db.session.flush()
    position.quantity = position.quantity - closing_quantity
    record_movement(
        position,
        PositionMovementKind.DECREASE,
        quantity_delta=-closing_quantity,
        price=exit_price,
        occurred_on=closed_on,
        result=result,
        transaction_id=transaction.id,
    )
    sync_open_transaction_for_position(position)
    return transaction
