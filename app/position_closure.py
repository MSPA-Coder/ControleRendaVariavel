"""Ciclo de vida de uma posição de ações: abertura, aumento, ajuste e
encerramento (total ou parcial).

Três registros andam juntos e são mantidos em dia aqui, nunca nas rotas:

- ``Position`` — o estado atual consolidado (uma linha por ticker, corretora,
  tipo e natureza);
- ``PositionMovement`` — o extrato que explica como se chegou a esse estado;
- ``Transaction`` — o que aparece na aba Transações: uma linha aberta
  espelhando a posição e uma linha fechada para cada parcela realizada.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, select

from app import db
from app.domain import (
    StatementEntry,
    is_duplicate_entry,
    plan_position_closure,
    replay_statement,
    weighted_average_cost,
)
from app.models import (
    Portfolio,
    Position,
    PositionMovement,
    PositionMovementKind,
    Transaction,
    TransactionStatus,
)
from app.position_ledger import archive_closed_position


def _is_simulated(portfolio_id: int) -> bool:
    """Se a carteira ``portfolio_id`` é a Simulada.

    Consulta direta em vez do relacionamento ``Position.portfolio_ref``:
    o ``candidate`` de ``create_or_merge_position`` ainda não foi
    adicionado à sessão no ponto em que isso precisa ser decidido, e um
    relacionamento não carrega por lazy load em um objeto transiente. A
    consulta é barata: ``Portfolio`` é uma tabela pequena e, nas rotas, o
    id já costuma estar no mapa de identidade da sessão (validado em
    ``_parse_form``/``_parse_position`` antes de chegar aqui).
    """

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
        portfolio_id=position.portfolio_id,
        source_position_id=position.id,
    )
    db.session.add(transaction)
    return transaction


def _open_transaction_for(position_id: int) -> Transaction | None:
    return db.session.scalar(
        select(Transaction).where(
            Transaction.source_position_id == position_id,
            Transaction.status == TransactionStatus.OPEN,
            # ``Position`` e ``OptionPosition`` têm sequências de id
            # independentes: sem este filtro, uma posição de opção com o
            # mesmo id numérico faria esta consulta encontrar duas linhas
            # (``MultipleResultsFound``) ou a linha errada.
            Transaction.ticker_id.is_not(None),
        )
    )


def sync_open_transaction_for_position(position: Position) -> None:
    """Mantém a linha aberta espelhada em dia após uma edição da posição.
    Cria a linha se, por algum motivo, ela ainda não existir.

    Posição simulada não tem transação. Ao trocar uma posição simulada para uma
    carteira real, o ramo "cria se não existir" abre a transação sem exigir um
    caminho dedicado."""

    if position.simulated:
        return
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
    transaction.portfolio_id = position.portfolio_id


def delete_open_transaction_for_position(position_id: int) -> None:
    """Remove a linha aberta espelhada quando a posição é excluída sem
    encerramento (ver ``routes.positions.delete_position``)."""

    transaction = _open_transaction_for(position_id)
    if transaction is not None:
        db.session.delete(transaction)


def record_movement(
    position: Position,
    kind: PositionMovementKind,
    *,
    quantity_delta: Decimal,
    price: Decimal,
    occurred_on: date,
    result: Decimal | None = None,
    transaction_id: int | None = None,
) -> PositionMovement:
    """Acrescenta um lançamento ao extrato da posição.

    O estado resultante é fotografado a partir da posição, que já deve estar
    atualizada quando esta função é chamada: é o custo médio vigente **depois**
    do movimento que interessa a quem lê o extrato.
    """

    movement = PositionMovement(
        position_id=position.id,
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


def replay_movements(position: Position) -> None:
    """Recalcula o saldo de cada movimento e o estado atual da posição.

    Necessário quando um movimento sai do meio do extrato — ao desfazer um
    encerramento parcial: os saldos gravados nos movimentos seguintes ainda
    descontariam uma parcela que não existe mais, e a posição continuaria
    reduzida. Reaplicar a cadeia inteira é o que faz extrato e posição voltarem
    a contar a mesma história.

    O custo médio é reaplicado junto porque um aumento o redefine; um
    encerramento parcial, não. O cálculo em si é ``domain.replay_statement``;
    esta função só traduz de/para os modelos ORM.
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


def partial_close_of_open_position(transaction: Transaction) -> Position | None:
    """A posição ainda aberta que esta transação encerrou parcialmente.

    ``None`` quando a transação não veio de uma posição, ainda está aberta ou
    encerrou a posição por inteiro — nesse caso ela é um registro histórico
    independente, e não o espelho de um movimento de uma posição viva.
    """

    if (
        transaction.status is not TransactionStatus.CLOSED
        or transaction.source_position_id is None
        or transaction.ticker_id is None
    ):
        return None
    return db.session.get(Position, transaction.source_position_id)


def revert_partial_close(transaction: Transaction) -> Position | None:
    """Desfaz o encerramento parcial que gerou esta transação.

    Devolve a quantidade encerrada à posição e remove a baixa do extrato, para
    que excluir a transação em Transações realmente volte ao estado anterior —
    em vez de apagar o registro da venda e deixar a posição reduzida.

    Não faz ``commit``: quem exclui a transação é dono do limite transacional.
    """

    position = db.session.scalar(
        select(Position)
        .where(Position.id == transaction.source_position_id)
        .with_for_update()
    )
    if position is None:
        return None
    movement = db.session.scalar(
        select(PositionMovement).where(PositionMovement.transaction_id == transaction.id)
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


def _mergeable_statement(candidate: Position) -> Select[tuple[Position]]:
    """Consulta da posição aberta que um novo aporte reforçaria.

    Unifica somente o que representa a mesma exposição: mesmo ticker, mesma
    corretora, mesmo tipo (compra ou venda) e mesma carteira. Uma compra não
    abate uma venda nem uma posição de uma carteira contamina a de outra —
    essas continuam sendo linhas separadas na carteira.
    """

    return (
        select(Position)
        .where(
            Position.broker_id == candidate.broker_id,
            Position.ticker_id == candidate.ticker_id,
            Position.side == candidate.side,
            Position.portfolio_id == candidate.portfolio_id,
        )
        .order_by(Position.id)
        .limit(1)
    )


def _mergeable_position(candidate: Position) -> Position | None:
    """A posição que o aporte vai reforçar, já travada para escrita.

    O ``FOR UPDATE`` existe porque dois aportes simultâneos no mesmo ativo
    leriam a mesma quantidade e o segundo sobrescreveria o primeiro.
    """

    return db.session.scalar(_mergeable_statement(candidate).with_for_update())


def duplicate_entry(candidate: Position) -> PositionMovement | None:
    """O último lançamento da posição, quando ele é idêntico a este aporte.

    Serve à confirmação extra no cadastro: dois cliques em Salvar produzem dois
    lançamentos iguais em milissegundos, e o segundo é indistinguível de um
    aporte real — ele soma quantidade e mexe no custo médio como qualquer
    outro. Comparar quantidade, preço e data com o último movimento é o que
    permite perguntar antes, em vez de deixar o usuário descobrir depois.

    Consulta sem lock: é uma leitura para decidir se pergunta algo ao usuário,
    não o começo da escrita.
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


def create_or_merge_position(candidate: Position) -> tuple[Position, bool]:
    """Registra um aporte, abrindo uma posição nova ou reforçando a existente.

    Devolve a posição persistida e se ela já existia. Quando já existia, o
    ``candidate`` é descartado: a quantidade é somada e o custo médio passa a
    ser a média ponderada dos dois (ver ``domain.weighted_average_cost``). A
    data inicial recua para a mais antiga das duas, porque a posição de fato é
    mantida desde o primeiro aporte; e os parâmetros da posição existente
    (delta da cotação, multiplicador do target e modo de resultado) são
    preservados, já que um aporte não é motivo para redefinir o alvo de uma
    posição em andamento.

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
    position: Position,
    previous_quantity: Decimal,
    previous_average_cost: Decimal,
) -> None:
    """Mantém o extrato coerente depois de uma edição direta da posição.

    Enquanto a posição só tem a abertura, editá-la é corrigir o próprio
    lançamento de abertura, e é isso que acontece. Depois que ela passou a ter
    histórico (aportes ou encerramentos parciais), reescrever a abertura
    falsificaria o passado: a diferença vira um movimento de ajuste, e o
    extrato continua explicando o estado atual.

    Posição simulada não tem extrato: não faz nada. Cobre tanto a
    edição comum de uma posição já simulada quanto a transição real ->
    simulada — o extrato existente é descartado à parte, por
    ``discard_simulation_history``, porque esta função só adiciona
    movimentos, nunca apaga.
    """

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


def discard_simulation_history(position: Position) -> None:
    """Descarta o histórico quando uma posição entra na
    carteira Simulada: apaga a linha ``status=OPEN`` que a espelhava em
    Transações e descarta o extrato inteiro — a carteira Simulada não tem
    nenhum dos dois.

    Chamada pela rota **depois** de a posição já ter o novo
    ``portfolio_id`` atribuído. A transição no sentido contrário (simulada
    -> real) não precisa de uma função dedicada: ``record_position_adjustment``
    recria a abertura do extrato (que está vazio) e
    ``sync_open_transaction_for_position`` cria a linha aberta que faltava —
    as duas já rodam depois de toda edição, e as guardas de ambas passam a
    deixá-las agir assim que a carteira deixa de ser a Simulada.
    """

    delete_open_transaction_for_position(position.id)
    for movement in list(position.movements):
        position.movements.remove(movement)


def close_open_position(
    position_id: int,
    exit_price: Decimal,
    closed_on: date,
    quantity: Decimal | None = None,
) -> Transaction | None:
    """Encerra uma posição aberta, por inteiro ou em parte, de forma atômica.
    Devolve ``None`` quando a posição não existe mais.

    Encerramento **total** (``quantity`` ausente ou igual à quantidade em
    carteira): reaproveita a linha ``status=OPEN`` já existente em
    ``transactions`` e a atualiza para ``status=CLOSED``, em vez de inserir uma
    nova — preserva o mesmo registro ao longo do ciclo de vida da posição — e
    apaga a posição, junto com o extrato de movimentos.

    Encerramento **parcial**: insere uma nova linha fechada com a quantidade
    realizada e reduz a linha aberta ao saldo que permanece na carteira, de
    modo que as duas somem a quantidade original. O custo médio do saldo não
    muda: vender parte da posição realiza resultado, não altera o que foi pago
    pelo que restou.

    A carteira Simulada não permite encerramento: a validação roda **depois**
    do ``FOR UPDATE`` (para travar antes de decidir) e faz ``rollback`` antes
    de propagar, liberando o lock sem deixar nada pela metade — mesmo
    padrão de erro que ``plan_position_closure`` já usa aqui embaixo.
    """

    position = db.session.scalar(
        select(Position).where(Position.id == position_id).with_for_update()
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
    position: Position, exit_price: Decimal, closed_on: date, result: Decimal
) -> Transaction:
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
    transaction.portfolio_id = position.portfolio_id
    transaction.notes = f"Encerrada a partir da posição #{position.id}."
    # Antes de apagar: a exclusão leva o extrato em cascata, e sem ele a
    # posição encerrada sumiria do relatório de performance, que reconstrói a
    # série a partir dele. Ver `app.position_ledger`.
    archive_closed_position(
        instrument="stock",
        position_id=position.id,
        ticker_id=position.ticker_id,
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
    position: Position,
    closing_quantity: Decimal,
    exit_price: Decimal,
    closed_on: date,
    result: Decimal,
) -> Transaction:
    transaction = Transaction(
        broker_id=position.broker_id,
        ticker_id=position.ticker_id,
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
        notes=f"Encerramento parcial da posição #{position.id}.",
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
