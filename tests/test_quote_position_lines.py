"""Linha de custo médio em degraus nos gráficos de cotação.

Cada posição vira uma linha só, que muda de nível quando o custo médio muda;
posição encerrada é um segmento no custo final, da abertura ao encerramento.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models import (
    Broker,
    Market,
    Portfolio,
    Position,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
    User,
)
from app.positions.average_cost_line import (
    Degrau,
    degraus_de_custo_medio,
    linhas_de_custo_medio_do_ticker,
)
from app.positions.closure import create_or_merge_position


def _degraus(*movimentos: tuple[date, str]) -> list[Degrau]:
    return degraus_de_custo_medio(
        [(dia, Decimal(custo)) for dia, custo in movimentos],
        abertura=date(2026, 1, 1),
        custo_atual=Decimal("99"),
    )


def test_so_abertura_e_um_degrau() -> None:
    assert _degraus((date(2026, 1, 10), "12.00")) == [Degrau(date(2026, 1, 10), Decimal("12.00"))]


def test_aumento_que_muda_o_custo_abre_degrau() -> None:
    assert _degraus((date(2026, 1, 10), "12.00"), (date(2026, 2, 12), "13.50")) == [
        Degrau(date(2026, 1, 10), Decimal("12.00")),
        Degrau(date(2026, 2, 12), Decimal("13.50")),
    ]


def test_venda_parcial_nao_muda_o_custo_nem_abre_degrau() -> None:
    assert _degraus(
        (date(2026, 1, 10), "12.00"),
        (date(2026, 2, 1), "12.00"),
        (date(2026, 3, 5), "14.00"),
    ) == [
        Degrau(date(2026, 1, 10), Decimal("12.00")),
        Degrau(date(2026, 3, 5), Decimal("14.00")),
    ]


def test_varios_movimentos_no_mesmo_dia_valem_pelo_ultimo() -> None:
    assert _degraus(
        (date(2026, 1, 10), "12.00"),
        (date(2026, 1, 10), "11.00"),
    ) == [Degrau(date(2026, 1, 10), Decimal("11.00"))]


def test_posicao_sem_extrato_e_um_nivel_no_custo_atual() -> None:
    """A carteira Simulada não tem extrato."""
    assert _degraus() == [Degrau(date(2026, 1, 1), Decimal("99"))]


@pytest.fixture
def cenario(sessao):
    usuario = User(username="degraus", password_hash="hash")
    corretora = Broker(name="Corretora degraus", acronym="CDGR")
    papel = Ticker(
        symbol="DGRS3", trading_name="Degraus S.A.", market=Market.B3,
        rtd_market_code="B", currency="BRL",
    )
    carteira = Portfolio(name="Carteira degraus", owner_ref=usuario, currency="BRL")
    simulada = Portfolio(name="Simulada degraus", owner_ref=usuario, currency="BRL", simulated=True)
    sessao.add_all([usuario, corretora, papel, carteira, simulada])
    sessao.flush()
    return usuario, corretora, papel, carteira, simulada


def _aporte(cenario, dia: date, quantidade: str, preco: str, carteira=None) -> Position:
    usuario, corretora, papel, real, _simulada = cenario
    posicao, _ = create_or_merge_position(
        Position(
            owner_id=usuario.id, broker_id=corretora.id, ticker_id=papel.id,
            portfolio_id=(carteira or real).id, quantity=Decimal(quantidade),
            average_cost=Decimal(preco), side=Side.BUY, opened_on=dia,
        )
    )
    return posicao


@pytest.mark.banco
def test_ticker_mostra_posicao_aberta_em_degraus_e_encerrada_em_segmento(sessao, cenario):
    usuario, corretora, papel, carteira, simulada = cenario
    _aporte(cenario, date(2026, 1, 10), "100", "10.00")
    _aporte(cenario, date(2026, 2, 12), "100", "20.00")
    _aporte(cenario, date(2026, 3, 1), "50", "8.00", carteira=simulada)
    sessao.add(
        Transaction(
            owner_id=usuario.id, broker_id=corretora.id, ticker_id=papel.id,
            portfolio_id=carteira.id, quantity=Decimal("10"), average_cost=Decimal("7.00"),
            exit_price=Decimal("9.00"), side=Side.BUY, opened_on=date(2025, 5, 2),
            closed_on=date(2025, 6, 30), result=Decimal("20.00"),
            status=TransactionStatus.CLOSED,
        )
    )
    sessao.flush()

    linhas = linhas_de_custo_medio_do_ticker(papel.id, usuario.id)

    assert [(linha["closed"], linha["until"], linha["steps"]) for linha in linhas] == [
        (
            False,
            None,
            [
                {"from": "2026-01-10", "averageCost": "10.00000000"},
                {"from": "2026-02-12", "averageCost": "15.00000000"},
            ],
        ),
        (True, "2025-06-30", [{"from": "2025-05-02", "averageCost": "7.00000000"}]),
    ]
    assert linhas_de_custo_medio_do_ticker(papel.id, usuario.id + 1000) == []
