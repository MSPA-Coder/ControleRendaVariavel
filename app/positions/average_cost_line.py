"""Linha de custo médio em degraus, desenhada sobre os gráficos de cotação.

Cada posição vira uma única linha: começa na abertura, no custo médio inicial,
e muda de nível na data de cada movimento que alterou o custo médio (aumento
ou ajuste manual). Encerramento parcial não altera o custo e, por isso, não
cria degrau. O nível vem de ``resulting_average_cost``, fotografado no extrato
no momento do movimento; nada é recalculado aqui.

Uma posição encerrada por inteiro perde o extrato (ver
``app.positions.closure._close_entirely``); da transação fechada só resta o
custo médio final. Ela aparece como um segmento reto nesse custo, da abertura
ao encerramento.

Só ações entram: o custo médio de uma opção é prêmio, em outra escala, e
achataria o gráfico do ativo-objeto.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app import db
from app.models import (
    Portfolio,
    Position,
    PositionMovement,
    PositionMovementKind,
    Transaction,
    TransactionStatus,
)


@dataclass(frozen=True)
class Degrau:
    desde: date
    custo_medio: Decimal


def degraus_de_custo_medio(
    movimentos: Iterable[tuple[date, Decimal]],
    *,
    abertura: date,
    custo_atual: Decimal,
) -> list[Degrau]:
    """Os níveis de custo médio de uma posição, em ordem cronológica.

    ``movimentos`` são pares (data, custo médio resultante) na ordem do
    extrato. Vários movimentos no mesmo dia valem pelo último, que é o estado
    ao fim do pregão. Movimento que não muda o custo não abre degrau.
    Posição sem extrato (a carteira Simulada não tem) é um único nível, da
    abertura ao custo atual.
    """
    por_dia: dict[date, Decimal] = {}
    for ocorrido_em, custo in movimentos:
        por_dia[ocorrido_em] = custo
    if not por_dia:
        return [Degrau(abertura, custo_atual)]
    degraus: list[Degrau] = []
    for dia in sorted(por_dia):
        if degraus and degraus[-1].custo_medio == por_dia[dia]:
            continue
        degraus.append(Degrau(dia, por_dia[dia]))
    return degraus


def linha_para_grafico(
    degraus: list[Degrau], *, rotulo: str, encerrada_em: date | None = None
) -> dict[str, object]:
    """Formato lido pelo navegador. Os valores seguem como texto decimal para
    não perder precisão antes de virarem coordenada."""
    return {
        "label": rotulo,
        "closed": encerrada_em is not None,
        "until": encerrada_em.isoformat() if encerrada_em is not None else None,
        "steps": [
            {"from": degrau.desde.isoformat(), "averageCost": str(degrau.custo_medio)}
            for degrau in degraus
        ],
    }


def degraus_da_posicao(position: Position) -> list[Degrau]:
    return degraus_de_custo_medio(
        (
            (movimento.occurred_on, movimento.resulting_average_cost)
            for movimento in position.movements
        ),
        abertura=position.opened_on,
        custo_atual=position.average_cost,
    )


@dataclass(frozen=True)
class Aporte:
    data: date
    preco: Decimal
    rotulo: str


def aportes_da_posicao(position: Position) -> list[Aporte]:
    """Abertura e aumentos do extrato, cada um no próprio preço: a referência
    de entrada que o custo médio, por ser média, esconde."""
    return [
        Aporte(
            movimento.occurred_on,
            movimento.price,
            ("Abertura" if movimento.kind is PositionMovementKind.OPEN else "Aumento")
            + f" em {movimento.occurred_on.strftime('%d/%m/%Y')}",
        )
        for movimento in position.movements
        if movimento.kind in (PositionMovementKind.OPEN, PositionMovementKind.INCREASE)
    ]


def linhas_de_custo_medio_do_ticker(ticker_id: int, owner_id: int) -> list[dict[str, object]]:
    """As linhas de custo médio do usuário no ticker: uma por posição real
    aberta e um segmento por transação de ações já encerrada.

    A carteira Simulada fica de fora: não é dinheiro investido, e os totais
    do projeto nunca misturam as duas naturezas.
    """
    posicoes = db.session.scalars(
        select(Position)
        .join(Portfolio, Position.portfolio_id == Portfolio.id)
        # O rótulo usa a corretora; sem isto, uma consulta por posição.
        .options(joinedload(Position.broker_ref))
        .where(
            Position.ticker_id == ticker_id,
            Position.owner_id == owner_id,
            Portfolio.simulated.is_(False),
        )
        .order_by(Position.opened_on, Position.id)
    ).all()
    movimentos: dict[int, list[tuple[date, Decimal]]] = {posicao.id: [] for posicao in posicoes}
    # Sempre uma consulta, mesmo sem posição: a contagem por tela fica fixa.
    for position_id, ocorrido_em, custo in db.session.execute(
        select(
            PositionMovement.position_id,
            PositionMovement.occurred_on,
            PositionMovement.resulting_average_cost,
        )
        .where(
            PositionMovement.position_id.in_(list(movimentos)),
            PositionMovement.owner_id == owner_id,
        )
        .order_by(PositionMovement.occurred_on, PositionMovement.id)
    ):
        movimentos[position_id].append((ocorrido_em, custo))
    encerradas = db.session.scalars(
        select(Transaction)
        .join(Portfolio, Transaction.portfolio_id == Portfolio.id)
        .where(
            Transaction.ticker_id == ticker_id,
            Transaction.owner_id == owner_id,
            Transaction.status == TransactionStatus.CLOSED,
            Portfolio.simulated.is_(False),
        )
        .order_by(Transaction.opened_on, Transaction.id)
    ).all()

    linhas = [
        linha_para_grafico(
            degraus_de_custo_medio(
                movimentos[posicao.id],
                abertura=posicao.opened_on,
                custo_atual=posicao.average_cost,
            ),
            rotulo=f"Custo médio · {posicao.broker}",
        )
        for posicao in posicoes
    ]
    for transacao in encerradas:
        assert transacao.closed_on is not None  # CHECK status_fields_consistency
        linhas.append(
            linha_para_grafico(
                [Degrau(transacao.opened_on, transacao.average_cost)],
                rotulo=(
                    f"Encerrada · {transacao.opened_on.strftime('%d/%m/%Y')}"
                    f" a {transacao.closed_on.strftime('%d/%m/%Y')}"
                ),
                encerrada_em=transacao.closed_on,
            )
        )
    return linhas
