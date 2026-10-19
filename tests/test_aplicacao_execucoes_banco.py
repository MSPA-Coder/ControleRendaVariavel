"""Execuções do myProfit levadas às posições, contra o PostgreSQL.

``apply_executions`` não faz commit (o comando decide): tudo aqui roda na
transação desfeita da fixture ``sessao``, e o ensaio é um ponto de salvamento
desfeito.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.executions.aplicacao import (
    ALREADY_ENTERED,
    APPLIED,
    BASELINE,
    PENDING,
    apply_executions,
    mark_baseline,
    pending_executions,
)
from app.executions.myprofit import read_execution_report
from app.executions.service import apply_import, plan_import
from app.models import (
    Broker,
    Execution,
    Market,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionType,
    Portfolio,
    Position,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
    User,
)
from tests.myprofit_sintetico import linha, relatorio_xlsx

pytestmark = pytest.mark.banco

D = Decimal


@pytest.fixture
def cenario(sessao, app_com_banco, monkeypatch):
    sufixo = uuid4().hex[:6].upper()
    dono = User(username=f"apl-{sufixo.lower()}", password_hash="hash")
    corretora = Broker(name=f"Genial {sufixo}", acronym=sufixo)
    sessao.add_all([dono, corretora])
    sessao.flush()
    carteira = Portfolio(name=f"BRL {sufixo}", owner_ref=dono, currency="BRL")
    dolar = Portfolio(name=f"USD {sufixo}", owner_ref=dono, currency="USD")
    sessao.add_all([carteira, dolar])

    # O código real de opção termina em dígito; com "E" no fim seria exercício.
    def ticker(nome):
        return Ticker(symbol=f"{nome}{sufixo}1", trading_name=nome, market=Market.B3, rtd_market_code="B", currency="BRL")

    acao, outra, opcao, sem_contrato = ticker("ACAO"), ticker("OUTRA"), ticker("OPC"), ticker("SEMC")
    sessao.add_all([acao, outra, opcao, sem_contrato])
    sessao.flush()
    vencimento = OptionExpiration(call_code=f"{sufixo[:4]}A", put_code=f"{sufixo[:4]}M", exercise_date=date(2031, 1, 17))
    sessao.add(vencimento)
    sessao.flush()
    contrato = OptionContract(
        ticker_id=opcao.id, underlying_ticker_id=acao.id, expiration_id=vencimento.id,
        option_type=OptionType.CALL, strike=D("12.00"),
    )
    sessao.add(contrato)
    sessao.flush()
    monkeypatch.setitem(app_com_banco.config, "MYPROFIT_INSTITUICOES", json.dumps({"GENIAL": corretora.name}))
    return {
        "dono": dono.id, "corretora": corretora.id, "carteira": carteira.id, "acao": acao, "outra": outra,
        "opcao": opcao, "contrato": contrato.id, "sem_contrato": sem_contrato, "sufixo": sufixo,
    }


def _importar(c, sessao, linhas):
    plano = plan_import(c["dono"], read_execution_report(relatorio_xlsx(linhas)))
    assert plano.problems == []
    apply_import(c["dono"], plano, file_name="sintetico.xlsx")
    sessao.flush()


def _posicao(sessao, c, ticker):
    return sessao.scalar(
        select(Position).where(Position.owner_id == c["dono"], Position.ticker_id == ticker.id)
    )


def _estados(relatorio):
    return [(o.trade.asset, o.state) for o in relatorio.outcomes]


def test_compra_abre_a_posicao_com_preco_medio_sem_taxas_e_nao_reaplica(sessao, cenario):
    c = cenario
    simbolo = c["acao"].symbol
    _importar(
        c,
        sessao,
        [
            # Uma ordem quebrada em duas linhas: um negócio só, preço ponderado.
            linha(date(2026, 11, 3), "B3_GENIAL_03112026", simbolo, 100, "-1000.00", taxas="0.30"),
            linha(date(2026, 11, 3), "B3_GENIAL_03112026", simbolo, 300, "-3600.00", taxas="0.90"),
        ],
    )

    relatorio = apply_executions(c["dono"])

    assert _estados(relatorio) == [(simbolo, APPLIED)]
    posicao = _posicao(sessao, c, c["acao"])
    assert (posicao.quantity, posicao.average_cost) == (D("400"), D("11.5"))
    assert posicao.portfolio_id == c["carteira"] and posicao.opened_on == date(2026, 11, 3)
    assert {e.application for e in sessao.scalars(select(Execution).where(Execution.owner_id == c["dono"]))} == {APPLIED}
    assert apply_executions(c["dono"]).outcomes == []


def test_ensaio_mostra_o_que_faria_e_nao_grava_nada(sessao, cenario):
    c = cenario
    _importar(c, sessao, [linha(date(2026, 11, 3), "B3_GENIAL_03112026", c["acao"].symbol, 100, "-1000.00")])

    ponto = sessao.begin_nested()
    ensaio = apply_executions(c["dono"])
    ponto.rollback()

    assert _estados(ensaio) == [(c["acao"].symbol, APPLIED)]
    assert _posicao(sessao, c, c["acao"]) is None
    assert len(pending_executions(c["dono"])) == 1


def test_venda_parcial_e_total_fecham_pelo_preco_medio_ponderado(sessao, cenario):
    c = cenario
    simbolo = c["acao"].symbol
    _importar(
        c,
        sessao,
        [
            linha(date(2026, 11, 3), "B3_GENIAL_03112026", simbolo, 400, "-4000.00"),
            linha(date(2026, 11, 10), "B3_GENIAL_10112026", simbolo, -100, "1300.00"),
            linha(date(2026, 11, 20), "B3_GENIAL_20112026", simbolo, -200, "2800.00"),
            linha(date(2026, 11, 20), "B3_GENIAL_20112026", simbolo, -100, "1300.00"),
        ],
    )

    relatorio = apply_executions(c["dono"])

    assert [o.state for o in relatorio.outcomes] == [APPLIED, APPLIED, APPLIED]
    assert _posicao(sessao, c, c["acao"]) is None
    fechadas = list(
        sessao.scalars(
            select(Transaction)
            .where(Transaction.owner_id == c["dono"], Transaction.status == TransactionStatus.CLOSED)
            .order_by(Transaction.closed_on)
        )
    )
    # 20/11: duas linhas de venda viram um encerramento a (200 x 14 + 100 x 13) / 300.
    assert [(t.closed_on, t.quantity, t.exit_price) for t in fechadas] == [
        (date(2026, 11, 10), D("100"), D("13")),
        (date(2026, 11, 20), D("300"), D("13.66666667")),
    ]
    assert all(t.average_cost == D("10") for t in fechadas)


def test_opcao_com_contrato_abre_a_posicao_de_opcao(sessao, cenario):
    c = cenario
    _importar(
        c, sessao, [linha(date(2026, 11, 3), "B3_GENIAL_03112026", c["opcao"].symbol, 1000, "-150.00", grupo="Opções")]
    )

    assert _estados(apply_executions(c["dono"])) == [(c["opcao"].symbol, APPLIED)]
    opcao = sessao.scalar(select(OptionPosition).where(OptionPosition.owner_id == c["dono"]))
    assert (opcao.contract_id, opcao.quantity, opcao.average_cost) == (c["contrato"], D("1000"), D("0.15"))


def test_negocio_ja_digitado_a_mao_so_e_marcado(sessao, cenario):
    c = cenario
    sessao.add(
        Position(
            owner_id=c["dono"], broker_id=c["corretora"], ticker_id=c["acao"].id, portfolio_id=c["carteira"],
            quantity=D("100"), average_cost=D("10"), side=Side.BUY, opened_on=date(2026, 11, 3),
        )
    )
    sessao.flush()
    from app.positions.closure import create_open_transaction_for_position

    create_open_transaction_for_position(_posicao(sessao, c, c["acao"]))
    sessao.flush()
    _importar(c, sessao, [linha(date(2026, 11, 3), "B3_GENIAL_03112026", c["acao"].symbol, 100, "-1000.00")])

    relatorio = apply_executions(c["dono"])

    assert _estados(relatorio) == [(c["acao"].symbol, ALREADY_ENTERED)]
    assert _posicao(sessao, c, c["acao"]).quantity == D("100")
    assert pending_executions(c["dono"]) == []


def test_o_que_pede_decisao_fica_pendente_e_o_resto_e_aplicado(sessao, cenario):
    c = cenario
    _importar(
        c,
        sessao,
        [
            linha(date(2026, 11, 3), "B3_GENIAL_03112026", f"NAOEXISTE{c['sufixo']}", 10, "-100.00"),
            linha(date(2026, 11, 3), "B3_GENIAL_03112026", c["sem_contrato"].symbol, 100, "-10.00", grupo="Opções"),
            linha(date(2026, 11, 4), "B3_GENIAL_04112026", f"{c['opcao'].symbol}E", 100, "-1200.00", grupo="Opções"),
            linha(date(2026, 11, 5), "B3_GENIAL_05112026", c["outra"].symbol, -50, "500.00"),
            linha(date(2026, 11, 6), "B3_GENIAL_06112026", c["acao"].symbol, 10, "-100.00"),
            linha(date(2026, 11, 7), "B3_GENIAL_07112026", c["acao"].symbol, -20, "220.00"),
        ],
    )

    relatorio = apply_executions(c["dono"])

    estados = dict(_estados(relatorio))
    assert estados[c["acao"].symbol] == PENDING  # a venda de 20 passa da posição de 10
    assert [o.state for o in relatorio.outcomes].count(APPLIED) == 1
    motivos = " | ".join(o.detail for o in relatorio.pending)
    for trecho in ("não está cadastrado", "sem contrato", "exercício", "venda sem posição", "passa da posição"):
        assert trecho in motivos
    assert _posicao(sessao, c, c["acao"]).quantity == D("10")
    assert len(pending_executions(c["dono"])) == 5


def test_linha_de_base_marca_sem_mexer_em_posicao(sessao, cenario):
    c = cenario
    _importar(
        c,
        sessao,
        [
            linha(date(2026, 10, 1), "B3_GENIAL_01102026", c["acao"].symbol, 100, "-1000.00"),
            linha(date(2026, 11, 4), "B3_GENIAL_04112026", f"{c['opcao'].symbol}E", 100, "-1200.00", grupo="Opções"),
        ],
    )

    assert mark_baseline(c["dono"], until=date(2026, 10, 31), note="conferido na conciliação de 30/09") == 1
    assert mark_baseline(c["dono"], until=date(2026, 11, 30), note="exercício lançado à mão", asset=f"{c['opcao'].symbol}e") == 1

    assert pending_executions(c["dono"]) == []
    assert {e.application for e in sessao.scalars(select(Execution).where(Execution.owner_id == c["dono"]))} == {BASELINE}
    assert _posicao(sessao, c, c["acao"]) is None


def test_reimportar_linha_ja_aplicada_que_mudou_e_recusado(sessao, cenario):
    c = cenario
    _importar(c, sessao, [linha(date(2026, 11, 3), "B3_GENIAL_03112026", c["acao"].symbol, 100, "-1000.00")])
    apply_executions(c["dono"])

    plano = plan_import(
        c["dono"],
        read_execution_report(relatorio_xlsx([linha(date(2026, 11, 3), "B3_GENIAL_03112026", c["acao"].symbol, 100, "-1001.00")])),
    )

    assert any("já está refletida" in problema for problema in plano.problems)
