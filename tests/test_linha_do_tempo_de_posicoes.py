"""A linha do tempo de posições é uma só, quem quer que a peça.

O CRV lia o extrato de posições em dois lugares: `app.routes.helpers.position_movement_events`
(o TWR da tela, preso à requisição por `current_owner_id()`) e
`app.patrimonio.queries.performance_events` (o v4, com o dono explícito). Eram cerca
de 270 linhas mantidas à parte, juntando os mesmos três extratos (ações vivas, opções
vivas e o arquivo das encerradas), com diferenças no tratamento da abertura: só o v4
dava uma abertura sintética à ação sem nenhum movimento.

Este arquivo prova que as duas respostas são a MESMA, para o mesmo dono e sem filtro,
num cenário que exercita o que poderia separá-las: ação com extrato, ação vendida,
ação legada sem nenhum movimento, opção com extrato, opção legada sem movimento,
posição encerrada (arquivo) e carteira simulada.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models import (
    Broker,
    Market,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionPositionMovement,
    OptionType,
    Portfolio,
    Position,
    PositionLedgerArchive,
    PositionMovement,
    PositionMovementKind,
    Side,
    Ticker,
    User,
)
from app.patrimonio import queries
from app.routes import helpers

pytestmark = pytest.mark.banco


def _ticker(simbolo):
    return Ticker(symbol=simbolo, trading_name=f"{simbolo} S.A.", market=Market.B3, rtd_market_code="B", currency="BRL")


@pytest.fixture
def cenario(sessao, monkeypatch):
    usuario = User(username="dono-da-linha", password_hash="hash")
    outro = User(username="outro-dono-da-linha", password_hash="hash")
    genial = Broker(name="Genial", acronym="GNL")
    xp = Broker(name="XP", acronym="XPI")
    wege, petr, vale, opcao = _ticker("WEGE3"), _ticker("PETR4"), _ticker("VALE3"), _ticker("VALEH100")
    sessao.add_all([usuario, outro, genial, xp, wege, petr, vale, opcao])
    sessao.flush()
    real = Portfolio(name="Real", owner_ref=usuario, currency="BRL", simulated=False)
    simulada = Portfolio(name="Simulada", owner_ref=usuario, currency="BRL", simulated=True)
    do_outro = Portfolio(name="Do outro", owner_ref=outro, currency="BRL", simulated=False)
    vencimento = OptionExpiration(call_code="HX", put_code="TX", exercise_date=date(2026, 8, 21))
    sessao.add_all([real, simulada, do_outro, vencimento])
    sessao.flush()
    contrato = OptionContract(
        ticker_id=opcao.id, underlying_ticker_id=vale.id, expiration_id=vencimento.id,
        option_type=OptionType.CALL, strike=Decimal("60.00"),
    )
    sessao.add(contrato)
    sessao.flush()

    def acao(ticker, corretora, carteira, lado, quantidade, aberta, dono=usuario):
        posicao = Position(
            owner_id=dono.id, broker_id=corretora.id, ticker_id=ticker.id, portfolio_id=carteira.id,
            quantity=Decimal(quantidade), average_cost=Decimal("10.00"), side=lado, opened_on=aberta,
        )
        sessao.add(posicao)
        sessao.flush()
        return posicao

    def movimento(posicao, tipo, delta, resultante, dia, dono=usuario):
        sessao.add(PositionMovement(
            owner_id=dono.id, position_id=posicao.id, kind=tipo, quantity_delta=Decimal(delta),
            price=Decimal("10.00"), occurred_on=dia, resulting_quantity=Decimal(resultante),
            resulting_average_cost=Decimal("10.00"),
            # O banco só aceita `result` na baixa (ck_position_movements_result_only_on_decrease).
            result=Decimal("0") if tipo == PositionMovementKind.DECREASE else None,
        ))

    # Ação comprada com extrato: abre com 100, aumenta para 150, reduz para 120.
    a = acao(wege, genial, real, Side.BUY, "120", date(2026, 1, 10))
    movimento(a, PositionMovementKind.OPEN, "100", "100", date(2026, 1, 10))
    movimento(a, PositionMovementKind.INCREASE, "50", "150", date(2026, 2, 3))
    movimento(a, PositionMovementKind.DECREASE, "-30", "120", date(2026, 3, 5))
    # Ação vendida a descoberto, em outra corretora.
    v = acao(petr, xp, real, Side.SELL, "200", date(2026, 1, 20))
    movimento(v, PositionMovementKind.OPEN, "200", "200", date(2026, 1, 20))
    # Ação legada: existe a posição, não existe nenhuma linha de extrato.
    acao(wege, xp, real, Side.BUY, "30", date(2026, 4, 1))
    # Carteira simulada e carteira de outro dono: ficam de fora.
    s = acao(petr, genial, simulada, Side.BUY, "999", date(2026, 2, 2))
    movimento(s, PositionMovementKind.OPEN, "999", "999", date(2026, 2, 2))
    acao(vale, genial, do_outro, Side.BUY, "5", date(2026, 2, 2), dono=outro)

    def opcao_viva(quantidade, aberta, extrato, corretora=genial):
        posicao = OptionPosition(
            owner_id=usuario.id, broker_id=corretora.id, contract_id=contrato.id, portfolio_id=real.id,
            quantity=Decimal(quantidade), average_cost=Decimal("1.50"), side=Side.SELL, opened_on=aberta,
        )
        sessao.add(posicao)
        sessao.flush()
        for tipo, delta, resultante, dia in extrato:
            sessao.add(OptionPositionMovement(
                owner_id=usuario.id, option_position_id=posicao.id, kind=tipo, quantity_delta=Decimal(delta),
                price=Decimal("1.50"), occurred_on=dia, resulting_quantity=Decimal(resultante),
                resulting_average_cost=Decimal("1.50"),
            ))
        return posicao

    # Opção com extrato e opção legada sem nenhuma linha de extrato.
    opcao_viva("300", date(2026, 5, 4), [
        (PositionMovementKind.OPEN, "200", "200", date(2026, 5, 4)),
        (PositionMovementKind.INCREASE, "100", "300", date(2026, 5, 18)),
    ])
    opcao_viva("50", date(2026, 6, 1), [], corretora=xp)
    # Posições já encerradas: só o arquivo guarda a história delas.
    for dia, quantidade in ((date(2026, 1, 5), "80"), (date(2026, 2, 9), "0")):
        sessao.add(PositionLedgerArchive(
            owner_id=usuario.id, occurred_on=dia, ticker_id=vale.id, portfolio_id=real.id,
            broker_id=genial.id, instrument="stock", source_position_id=901,
            resulting_signed_quantity=Decimal(quantidade),
        ))
    sessao.add(PositionLedgerArchive(
        owner_id=usuario.id, occurred_on=date(2026, 3, 3), ticker_id=opcao.id, portfolio_id=real.id,
        broker_id=xp.id, instrument="option", source_position_id=902,
        resulting_signed_quantity=Decimal("-10"),
    ))
    sessao.flush()
    monkeypatch.setattr(helpers, "current_owner_id", lambda: usuario.id)
    return {
        "usuario": usuario, "genial": genial, "xp": xp, "real": real, "simulada": simulada,
        "wege": wege, "petr": petr, "vale": vale, "opcao": opcao,
    }


def _como_tupla(eventos):
    return sorted(
        (e.occurred_on, e.ticker_id, e.resulting_signed_quantity, e.position_key) for e in eventos
    )


def test_a_tela_e_o_v4_leem_a_mesma_linha_do_tempo(cenario):
    da_tela = helpers.position_movement_events()
    do_v4 = queries.performance_events(date(2030, 1, 1), cenario["usuario"].id)

    assert _como_tupla(da_tela) == _como_tupla(do_v4)


def test_o_cenario_exercita_o_que_poderia_separa_las(cenario):
    """Sem isto, a igualdade acima poderia ser a de duas listas quase vazias."""
    eventos = queries.performance_events(date(2030, 1, 1), cenario["usuario"].id)
    chaves = {e.position_key[0] for e in eventos}
    quantidades = {e.resulting_signed_quantity for e in eventos}

    assert chaves == {"stock", "option"}
    assert Decimal("-200") in quantidades  # a vendida
    assert Decimal("30") in quantidades  # a ação legada, com a abertura sintética
    assert Decimal("-50") in quantidades  # a opção vendida legada, com a abertura sintética
    assert Decimal("999") not in quantidades  # a simulada
    assert Decimal("5") not in quantidades  # a do outro dono
    assert len(eventos) == 11


def test_a_acao_legada_sem_extrato_tem_a_abertura_sintetica_tambem_na_tela(cenario):
    """Antes só o v4 a dava; a tela a omitia, e o TWR ignorava a posição."""
    da_tela = {(e.position_key[0], e.resulting_signed_quantity) for e in helpers.position_movement_events()}

    assert ("stock", Decimal("30")) in da_tela


def test_filtro_de_corretora_da_tela(cenario):
    eventos = helpers.position_movement_events(broker="XP")

    # A vendida (PETR4), a ação legada (WEGE3), a opção legada e o arquivo da opção encerrada.
    assert {(e.ticker_id, e.resulting_signed_quantity) for e in eventos} == {
        (cenario["petr"].id, Decimal("-200")),
        (cenario["wege"].id, Decimal("30")),
        (cenario["opcao"].id, Decimal("-50")),
        (cenario["opcao"].id, Decimal("-10")),
    }


def test_filtro_de_carteira_da_tela(cenario):
    assert len(helpers.position_movement_events(portfolio_id=cenario["real"].id)) == 11
    # A simulada nunca entra, nem pedida pelo id.
    assert helpers.position_movement_events(portfolio_id=cenario["simulada"].id) == []


def test_sem_opcoes_a_tela_le_so_acoes_e_o_arquivo_das_acoes(cenario):
    eventos = helpers.position_movement_events(include_options=False)

    assert {e.position_key[0] for e in eventos} == {"stock"}
    assert len(eventos) == 7  # ações vivas (3 + 1 + 1) e o arquivo da ação encerrada (2)


def test_filtro_de_ativos_da_tela(cenario):
    so_petr = helpers.position_movement_events(ticker_ids=[cenario["petr"].id])

    assert {e.ticker_id for e in so_petr} == {cenario["petr"].id}
    assert len(so_petr) == 1  # a vendida; a da carteira simulada não entra
    assert helpers.position_movement_events(ticker_ids=[]) == []


def test_o_corte_por_data_do_v4_nao_perde_o_que_veio_antes(cenario):
    ate_fevereiro = queries.performance_events(date(2026, 2, 28), cenario["usuario"].id)

    assert max(e.occurred_on for e in ate_fevereiro) <= date(2026, 2, 28)
    assert Decimal("150") in {e.resulting_signed_quantity for e in ate_fevereiro}
    assert Decimal("120") not in {e.resulting_signed_quantity for e in ate_fevereiro}
