"""Desfazer o encerramento TOTAL de uma posição (ação ou opção).

Encerrar por inteiro apaga a posição e o extrato dela. O que sobra:

- a transação fechada (a mesma linha que espelhava a posição aberta);
- ``position_ledger_archive``: a projeção mínima que alimenta a série de
  performance, para ação e opção;
- o arquivo completo do extrato (``position_movement_archive`` para ações desde
  ``20260928_0024``; ``option_movement_archive`` para opções desde
  ``20261111_0046``), cuja última linha, ``close``, aponta para a transação.

Excluir essa transação em Transações significa desfazer o encerramento, como já
significa no encerramento parcial. Dois desfechos, escolhidos pelo que existe:

- **arquivo completo**: a posição e o extrato voltam ao estado imediatamente
  anterior ao encerramento (mesmos ids, datas, quantidades e custos médios), a
  transação volta a ser a linha aberta e as linhas dos dois arquivos saem, na
  mesma transação de banco. Se já existir posição viva com a mesma chave
  (dono, carteira, corretora, ativo, lado), nada se funde nem se adivinha:
  recusa com mensagem.
- **sem arquivo completo** (encerramentos antigos): a posição não tem de onde
  voltar. A transação é apagada e as linhas da posição saem de
  ``position_ledger_archive``, para a série de performance não contar uma
  posição que não volta. Exige confirmação explícita.

O que o arquivo não guarda não volta: o alvo da posição (``target_multiplier``
das ações, ``target_price`` das opções) retorna ao padrão.

Não faz ``commit``: quem exclui a transação é dono do limite transacional.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from sqlalchemy import delete, select

from app import db
from app.accounts.auditoria import registrar
from app.models import (
    OptionMovementArchive,
    OptionPosition,
    OptionPositionMovement,
    Position,
    PositionLedgerArchive,
    PositionMovement,
    PositionMovementArchive,
    PositionMovementKind,
    Side,
    Transaction,
    TransactionStatus,
)
from app.options import closure as option_closure
from app.positions import closure as stock_closure

#: Texto que ``_close_entirely`` grava nas notas; só entra na detecção dos
#: encerramentos sem arquivo completo (ver :func:`classify`).
NOTA_STOCK = "Encerrada a partir da posição #"
NOTA_OPTION = "Encerrada a partir da posição de opção #"


class SemExtratoSemConfirmacaoError(ValueError):
    """Encerramento sem arquivo completo: precisa da confirmação do usuário."""


class PosicaoVivaError(ValueError):
    """Já existe posição viva com a mesma chave: nada se funde nem se adivinha."""


@dataclass(frozen=True, slots=True)
class _Lado:
    instrument: str
    position_model: type
    movement_model: type
    movement_fk: str
    archive_model: type
    closure: ModuleType
    note: str
    label: str


STOCK = _Lado(
    "stock", Position, PositionMovement, "position_id", PositionMovementArchive,
    stock_closure, NOTA_STOCK, "ação",
)
OPTION = _Lado(
    "option", OptionPosition, OptionPositionMovement, "option_position_id", OptionMovementArchive,
    option_closure, NOTA_OPTION, "opção",
)


def _lado(transaction: Transaction) -> _Lado:
    return OPTION if transaction.option_contract_id is not None else STOCK


@dataclass(frozen=True, slots=True)
class UndoOutcome:
    reopened: bool
    position_id: int
    movements: int


def classify(transactions: Sequence[Transaction]) -> dict[int, bool]:
    """Quais transações fechadas são o encerramento TOTAL de uma posição que já não existe.

    ``{id da transação: True}`` quando o arquivo completo do extrato tem a linha
    ``close`` dela (dá para reabrir); ``False`` quando só a projeção da série
    sobrou (encerramento antigo: não dá). Ausente: não é um encerramento total
    (transação lançada à mão, baixa parcial, posição ainda viva).

    Sem arquivo completo a transação é reconhecida pelo conjunto: a posição não
    existe, as notas são as do encerramento total, a série zera na data da
    transação e o saldo anterior é a quantidade dela. Uma baixa parcial no mesmo
    dia do fechamento, ou uma nota editada, ficam de fora de propósito: nesses
    casos exclui-se a transação como antes, sem tocar em arquivo nenhum.

    Poucas consultas por chamada (por instrumento e dono), nunca uma por linha.
    """
    resultado: dict[int, bool] = {}
    grupos: dict[tuple[str, int], list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        if transaction.status is TransactionStatus.CLOSED and transaction.source_position_id is not None:
            grupos[(_lado(transaction).instrument, transaction.owner_id)].append(transaction)
    for (instrument, owner_id), grupo in grupos.items():
        lado = STOCK if instrument == "stock" else OPTION
        ids_posicao = {t.source_position_id for t in grupo}
        vivas = set(db.session.scalars(
            select(lado.position_model.id).where(
                lado.position_model.owner_id == owner_id, lado.position_model.id.in_(ids_posicao)
            )
        ))
        sem_posicao = [t for t in grupo if t.source_position_id not in vivas]
        if not sem_posicao:
            continue
        com_arquivo = set(db.session.scalars(
            select(lado.archive_model.source_transaction_id).where(
                lado.archive_model.owner_id == owner_id,
                lado.archive_model.kind == "close",
                lado.archive_model.source_transaction_id.in_([t.id for t in sem_posicao]),
            )
        ))
        legado = []
        for transaction in sem_posicao:
            if transaction.id in com_arquivo:
                resultado[transaction.id] = True
            elif (transaction.notes or "").startswith(lado.note):
                legado.append(transaction)
        if not legado:
            continue
        serie: dict[int, list[PositionLedgerArchive]] = defaultdict(list)
        for linha in db.session.scalars(
            select(PositionLedgerArchive)
            .where(
                PositionLedgerArchive.owner_id == owner_id,
                PositionLedgerArchive.instrument == instrument,
                PositionLedgerArchive.source_position_id.in_({t.source_position_id for t in legado}),
            )
            .order_by(PositionLedgerArchive.id)
        ):
            serie[linha.source_position_id].append(linha)
        for transaction in legado:
            linhas = serie.get(transaction.source_position_id, [])
            if (
                len(linhas) >= 2
                and linhas[-1].resulting_signed_quantity == 0
                and linhas[-1].occurred_on == transaction.closed_on
                and abs(linhas[-2].resulting_signed_quantity) == transaction.quantity
            ):
                resultado[transaction.id] = False
    return resultado


def undo_total_closure(transaction: Transaction, *, accept_without_extract: bool) -> UndoOutcome | None:
    """Desfaz o encerramento total que esta transação representa; ``None`` se não for um.

    Levanta :class:`PosicaoVivaError` (posição viva com a mesma chave),
    :class:`SemExtratoSemConfirmacaoError` (sem arquivo completo e sem
    confirmação) ou ``ValueError`` (arquivo inconsistente). Em todos, nada foi
    gravado; quem chama faz o rollback.
    """
    tipo = classify([transaction]).get(transaction.id)
    if tipo is None:
        return None
    lado = _lado(transaction)
    if tipo:
        return _reabrir(lado, transaction)
    if not accept_without_extract:
        raise SemExtratoSemConfirmacaoError(
            "O extrato deste encerramento não foi guardado: a posição não será reaberta."
        )
    return _descartar(lado, transaction)


def _remover_linhas_da_posicao(lado: _Lado, owner_id: int, position_id: int) -> None:
    db.session.execute(
        delete(PositionLedgerArchive).where(
            PositionLedgerArchive.owner_id == owner_id,
            PositionLedgerArchive.instrument == lado.instrument,
            PositionLedgerArchive.source_position_id == position_id,
        )
    )


def _descartar(lado: _Lado, transaction: Transaction) -> UndoOutcome:
    position_id = int(transaction.source_position_id or 0)
    _remover_linhas_da_posicao(lado, transaction.owner_id, position_id)
    registrar(
        "OptionPosition" if lado.instrument == "option" else "Position",
        "desfazer",
        entidade_id=position_id,
        detalhes={"transacao": transaction.id, "reaberta": False, "motivo": "sem arquivo do extrato"},
    )
    db.session.delete(transaction)
    return UndoOutcome(False, position_id, 0)


def _nova_posicao(lado: _Lado, transaction: Transaction, fechamento: Any) -> Any:
    comum: dict[str, Any] = {
        "id": transaction.source_position_id,
        "owner_id": transaction.owner_id,
        "broker_id": fechamento.broker_id,
        "portfolio_id": fechamento.portfolio_id,
        "side": Side(fechamento.side),
        "quantity": -fechamento.quantity_delta,
        "average_cost": fechamento.resulting_average_cost,
        "opened_on": transaction.opened_on,
    }
    if lado.instrument == "option":
        return OptionPosition(contract_id=fechamento.option_contract_id, **comum)
    return Position(ticker_id=fechamento.ticker_id, **comum)


def _reabrir(lado: _Lado, transaction: Transaction) -> UndoOutcome:
    owner_id = transaction.owner_id
    position_id = int(transaction.source_position_id or 0)
    arquivo = list(db.session.scalars(
        select(lado.archive_model)
        .where(lado.archive_model.owner_id == owner_id, lado.archive_model.source_position_id == position_id)
        .order_by(lado.archive_model.id)
    ))
    fechamento = arquivo[-1]
    movimentos = arquivo[:-1]
    if fechamento.kind != "close" or fechamento.source_transaction_id != transaction.id or any(
        m.kind == "close" for m in movimentos
    ):
        raise ValueError("O arquivo do extrato desta posição não bate com a transação; nada foi desfeito.")
    if movimentos and movimentos[-1].resulting_quantity != -fechamento.quantity_delta:
        raise ValueError("O arquivo do extrato desta posição está inconsistente; nada foi desfeito.")

    posicao = _nova_posicao(lado, transaction, fechamento)
    # Mesma trava e mesma consulta de um novo aporte: serializa com quem cria ou
    # reforça a posição e acha a que já existe com a chave.
    if lado.closure._mergeable_position(posicao) is not None:
        raise PosicaoVivaError(
            f"Já existe uma posição de {lado.label} aberta com a mesma carteira, corretora e tipo "
            "(reaberta ou reforçada depois deste encerramento). Encerre ou exclua essa posição "
            "em Carteira antes de desfazer este encerramento."
        )
    db.session.add(posicao)
    db.session.flush()
    for linha in movimentos:
        transacao_da_baixa = None
        if linha.source_transaction_id is not None and db.session.scalar(
            select(Transaction.id).where(
                Transaction.id == linha.source_transaction_id, Transaction.owner_id == owner_id
            )
        ):
            transacao_da_baixa = linha.source_transaction_id
        db.session.add(
            lado.movement_model(
                **{lado.movement_fk: posicao.id},
                id=linha.source_movement_id,
                owner_id=owner_id,
                kind=PositionMovementKind(linha.kind),
                quantity_delta=linha.quantity_delta,
                price=linha.price,
                occurred_on=linha.occurred_on,
                result=linha.result,
                transaction_id=transacao_da_baixa,
                resulting_quantity=linha.resulting_quantity,
                resulting_average_cost=linha.resulting_average_cost,
                created_at=linha.source_created_at,
            )
        )
    # A transação volta a ser o espelho da posição aberta, a mesma linha que o
    # encerramento tinha fechado.
    transaction.status = TransactionStatus.OPEN
    transaction.exit_price = None
    transaction.closed_on = None
    transaction.result = None
    transaction.notes = None
    transaction.quantity = posicao.quantity
    transaction.average_cost = posicao.average_cost
    transaction.opened_on = posicao.opened_on
    db.session.flush()
    db.session.execute(
        delete(lado.archive_model).where(
            lado.archive_model.owner_id == owner_id, lado.archive_model.source_position_id == position_id
        )
    )
    _remover_linhas_da_posicao(lado, owner_id, position_id)
    registrar(
        "OptionPosition" if lado.instrument == "option" else "Position",
        "desfazer",
        entidade_id=posicao.id,
        detalhes={
            "transacao": transaction.id,
            "reaberta": True,
            "movimentos": len(movimentos),
            "quantidade": str(posicao.quantity),
        },
    )
    return UndoOutcome(True, posicao.id, len(movimentos))
