"""Proventos do CB no banco: importação idempotente, substituição e desfazer.

Camada com banco porque as garantias moram nele: índice único parcial por
lançamento do CB, a chave de importação que vale só para a planilha, as
`CheckConstraint` das colunas novas e a atomicidade do que o comando grava.
Cenário sintético: nenhum valor real.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.dividends.cb_sync import (
    apply_cb_import,
    apply_substitution,
    plan_cb_import,
    plan_substitution,
    undo_substitution,
)
from app.models import (
    Broker,
    Dividend,
    IncomeKind,
    Market,
    Ticker,
    User,
    UserTickerEntitlement,
)
from app.patrimonio.cb_client import CbIncome
from tests.proventos_cb_sintetico import XP, lancamento

pytestmark = pytest.mark.banco

D = Decimal
XP_INST = {"conta": XP, "instituicao": "scp-xp-investimentos"}


def _ticker(sessao, simbolo, moeda="BRL"):
    ticker = Ticker(symbol=simbolo, trading_name=f"{simbolo} S.A.", market=Market.B3, rtd_market_code="B", currency=moeda)
    sessao.add(ticker)
    return ticker


@pytest.fixture
def cenario(sessao):
    usuario = User(username="dono-cb", password_hash="hash")
    outro = User(username="outro-cb", password_hash="hash")
    genial = Broker(name="Genial", acronym="GNL")
    xp = Broker(name="XP", acronym="XPI")
    nomad = Broker(name="Nomad", acronym="NMD")
    tickers = {s: _ticker(sessao, s) for s in ("RAIZ4", "SMTO3", "EALT4", "BBAS3")}
    tickers["AAPL"] = _ticker(sessao, "AAPL", "USD")
    sessao.add_all([usuario, outro, genial, xp, nomad])
    sessao.flush()
    return {"usuario": usuario, "outro": outro, "genial": genial, "xp": xp, "nomad": nomad, "tickers": tickers}


def _renda(*entradas):
    return CbIncome(entries=list(entradas))


def _entradas_de_janeiro():
    dia = date(2026, 1, 29)
    return [
        lancamento(10, "credito", "352.56", dia, ativo="RAIZ4"),
        lancamento(11, "irrf_aluguel", "79.32", dia, ativo="RAIZ4"),
        lancamento(12, "corretagem_aluguel", "105.76", dia),
        lancamento(20, "credito", "920.78", date(2026, 3, 10), ativo="EALT4", kind="dividendo", **XP_INST),
    ]


def _linhas_do_cb(sessao, dono):
    return list(
        sessao.scalars(
            select(Dividend).where(Dividend.owner_id == dono.id, Dividend.origin == "cb").order_by(Dividend.payment_date)
        )
    )


def test_importar_grava_liquido_bruto_e_retencoes_com_a_corretora_e_o_ativo_certos(sessao, cenario):
    plano = plan_cb_import(cenario["usuario"].id, _renda(*_entradas_de_janeiro()))

    criadas, atualizadas = apply_cb_import(cenario["usuario"].id, plano)

    assert (criadas, atualizadas) == (2, 0)
    aluguel, dividendo = _linhas_do_cb(sessao, cenario["usuario"])
    assert (aluguel.amount, aluguel.gross_amount, aluguel.withheld_amount) == (D("167.48"), D("352.56"), D("185.08"))
    assert (aluguel.origin_id, aluguel.kind, aluguel.broker, aluguel.ticker) == ("10", IncomeKind.ALUGUEL, "Genial", "RAIZ4")
    assert aluguel.payment_date == date(2026, 1, 29)
    assert (dividendo.amount, dividendo.gross_amount, dividendo.withheld_amount) == (D("920.78"), D("920.78"), D("0"))
    assert (dividendo.broker, dividendo.kind) == ("XP", IncomeKind.DIVIDENDO)
    # O provento garante a consulta de cotação do ativo, como as demais gravações de renda.
    assert sessao.get(UserTickerEntitlement, (cenario["usuario"].id, cenario["tickers"]["RAIZ4"].id)) is not None


def test_reexecutar_nao_duplica_nem_altera(sessao, cenario):
    dono = cenario["usuario"]
    apply_cb_import(dono.id, plan_cb_import(dono.id, _renda(*_entradas_de_janeiro())))
    antes = [(r.id, r.amount, r.gross_amount, r.withheld_amount) for r in _linhas_do_cb(sessao, dono)]

    segundo = plan_cb_import(dono.id, _renda(*_entradas_de_janeiro()))

    assert (len(segundo.create), len(segundo.update), segundo.unchanged) == (0, 0, 2)
    assert not segundo.can_write
    assert [(r.id, r.amount, r.gross_amount, r.withheld_amount) for r in _linhas_do_cb(sessao, dono)] == antes


def test_lancamento_corrigido_no_cb_atualiza_a_linha_em_vez_de_duplicar(sessao, cenario):
    dono = cenario["usuario"]
    apply_cb_import(dono.id, plan_cb_import(dono.id, _renda(*_entradas_de_janeiro())))
    corrigido = _entradas_de_janeiro()
    corrigido[2] = lancamento(12, "corretagem_aluguel", "100.00", date(2026, 1, 29))

    plano = plan_cb_import(dono.id, _renda(*corrigido))
    assert (len(plano.create), len(plano.update), plano.unchanged) == (0, 1, 1)
    assert apply_cb_import(dono.id, plano) == (0, 1)

    aluguel = _linhas_do_cb(sessao, dono)[0]
    assert (aluguel.amount, aluguel.withheld_amount) == (D("173.24"), D("179.32"))
    assert len(_linhas_do_cb(sessao, dono)) == 2


def test_so_toca_linhas_do_cb_e_so_do_dono(sessao, cenario):
    dono, outro = cenario["usuario"], cenario["outro"]
    planilha = Dividend(
        owner_id=dono.id, broker_id=cenario["genial"].id, ticker_id=cenario["tickers"]["RAIZ4"].id,
        kind=IncomeKind.ALUGUEL, amount=D("202.73"), payment_date=date(2026, 1, 29),
    )
    antiga = Dividend(
        owner_id=dono.id, broker_id=cenario["genial"].id, ticker_id=cenario["tickers"]["BBAS3"].id,
        kind=IncomeKind.DIVIDENDO, amount=D("30.00"), payment_date=date(2025, 12, 15),
    )
    de_outro = Dividend(
        owner_id=outro.id, broker_id=cenario["genial"].id, ticker_id=cenario["tickers"]["RAIZ4"].id,
        kind=IncomeKind.ALUGUEL, amount=D("1.00"), payment_date=date(2026, 1, 29),
    )
    sessao.add_all([planilha, antiga, de_outro])
    sessao.flush()

    apply_cb_import(dono.id, plan_cb_import(dono.id, _renda(*_entradas_de_janeiro())))

    assert sessao.scalar(select(func.count(Dividend.id)).where(Dividend.owner_id == dono.id, Dividend.origin == "planilha")) == 2
    assert sessao.scalar(select(func.count(Dividend.id)).where(Dividend.owner_id == outro.id)) == 1
    assert (planilha.amount, planilha.superseded_at, antiga.amount) == (D("202.73"), None, D("30.00"))
    assert _linhas_do_cb(sessao, outro) == []


def test_ticker_desconhecido_vai_para_a_fila_e_o_comando_nao_cria_ticker(sessao, cenario):
    dono = cenario["usuario"]
    tickers_antes = sessao.scalar(select(func.count(Ticker.id)))

    plano = plan_cb_import(
        dono.id, _renda(lancamento(30, "credito", "247.69", date(2026, 8, 12), ativo="WEGE3", kind="dividendo"))
    )
    apply_cb_import(dono.id, plano)

    assert [item.entry_id for item in plano.income.review] == ["30"]
    assert plano.create == []
    assert sessao.scalar(select(func.count(Ticker.id))) == tickers_antes
    assert _linhas_do_cb(sessao, dono) == []


def test_credito_que_sumiu_do_cb_e_listado_sem_apagar_a_linha(sessao, cenario):
    dono = cenario["usuario"]
    apply_cb_import(dono.id, plan_cb_import(dono.id, _renda(*_entradas_de_janeiro())))

    plano = plan_cb_import(dono.id, _renda(*_entradas_de_janeiro()[3:]))

    assert [linha.origin_id for linha in plano.absent_from_cb] == ["10"]
    assert len(_linhas_do_cb(sessao, dono)) == 2


def test_corretora_sem_cadastro_no_crv_impede_a_gravacao(sessao, cenario):
    sessao.delete(cenario["xp"])
    sessao.flush()

    plano = plan_cb_import(cenario["usuario"].id, _renda(*_entradas_de_janeiro()))

    assert plano.problems and "XP" in plano.problems[0]


# --- índices e restrições ------------------------------------------------------------


def _linha_cb(c, origin_id, valor="10.00", ticker="BBAS3", dia=date(2026, 6, 11), kind=IncomeKind.JCP):
    return Dividend(
        owner_id=c["usuario"].id, broker_id=c["genial"].id, ticker_id=c["tickers"][ticker].id, kind=kind,
        amount=D(valor), gross_amount=D(valor), withheld_amount=D("0"), payment_date=dia,
        origin="cb", origin_id=origin_id,
    )


def test_dois_creditos_iguais_do_cb_no_mesmo_dia_sao_dois_lancamentos(sessao, cenario):
    """Antes da revisão 0032 a chave de importação da planilha recusava o segundo."""
    sessao.add_all([_linha_cb(cenario, "100"), _linha_cb(cenario, "101")])

    sessao.flush()


def test_o_mesmo_lancamento_do_cb_nao_entra_duas_vezes(sessao, cenario):
    sessao.add_all([_linha_cb(cenario, "100"), _linha_cb(cenario, "100", valor="11.00")])

    with pytest.raises(IntegrityError):
        sessao.flush()


def test_a_chave_de_importacao_continua_valendo_para_a_planilha(sessao, cenario):
    def planilha():
        return Dividend(
            owner_id=cenario["usuario"].id, broker_id=cenario["genial"].id,
            ticker_id=cenario["tickers"]["BBAS3"].id, kind=IncomeKind.JCP, amount=D("10.00"),
            payment_date=date(2025, 6, 11),
        )

    sessao.add_all([planilha(), planilha()])

    with pytest.raises(IntegrityError):
        sessao.flush()


@pytest.mark.parametrize(
    "campos",
    [
        {"origin": "outra"},
        {"origin": "cb", "origin_id": None},
        {"gross_amount": D("0")},
        {"withheld_amount": D("-0.01")},
        {"origin": "cb", "origin_id": "9", "superseded_at": func.now()},
        {"gross_amount": D("NaN")},
    ],
)
def test_o_banco_recusa_combinacoes_invalidas_de_origem_bruto_e_retencao(sessao, cenario, campos):
    base = {
        "owner_id": cenario["usuario"].id, "broker_id": cenario["genial"].id,
        "ticker_id": cenario["tickers"]["BBAS3"].id, "kind": IncomeKind.JCP,
        "amount": D("5.00"), "payment_date": date(2026, 6, 11),
    }
    sessao.add(Dividend(**{**base, **campos}))

    with pytest.raises(IntegrityError):
        sessao.flush()


# --- substituição da planilha -------------------------------------------------------


def _linha_planilha(c, ticker, valor, dia, *, corretora="genial", kind=IncomeKind.ALUGUEL):
    return Dividend(
        owner_id=c["usuario"].id, broker_id=c[corretora].id, ticker_id=c["tickers"][ticker].id, kind=kind,
        amount=D(valor), payment_date=dia,
    )


def _cenario_de_substituicao(sessao, c):
    dono = c["usuario"]
    apply_cb_import(dono.id, plan_cb_import(dono.id, _renda(*_entradas_de_janeiro())))
    pareada = _linha_planilha(c, "RAIZ4", "202.72", date(2026, 1, 27))  # 57,5% de 352,56, 2 dias antes
    dividendo = _linha_planilha(c, "EALT4", "920.78", date(2026, 3, 10), corretora="xp", kind=IncomeKind.DIVIDENDO)
    divergente = _linha_planilha(c, "SMTO3", "5.00", date(2026, 2, 3))
    dolar = _linha_planilha(c, "AAPL", "3.00", date(2026, 2, 3), corretora="nomad", kind=IncomeKind.DIVIDENDO)
    anterior = _linha_planilha(c, "RAIZ4", "50.00", date(2025, 12, 20))
    sessao.add_all([pareada, dividendo, divergente, dolar, anterior])
    sessao.flush()
    return {"pareada": pareada, "dividendo": dividendo, "divergente": divergente, "dolar": dolar, "anterior": anterior}


def test_substituicao_marca_so_a_planilha_pareada_em_reais_a_partir_do_corte(sessao, cenario):
    linhas = _cenario_de_substituicao(sessao, cenario)
    dono = cenario["usuario"]

    plano = plan_substitution(dono.id)

    assert {linha.id for linha in plano.to_supersede} == {linhas["pareada"].id, linhas["dividendo"].id}
    assert [linha.id for linha in plano.divergences] == [linhas["divergente"].id]
    assert plano.eligible == 3  # a de dólar e a anterior ao corte nem entram
    assert apply_substitution(dono.id, plano) == 2

    assert linhas["pareada"].superseded_at is not None and linhas["dividendo"].superseded_at is not None
    for intacta in ("divergente", "dolar", "anterior"):
        assert linhas[intacta].superseded_at is None, intacta
    assert all(linha.superseded_at is None for linha in _linhas_do_cb(sessao, dono))
    # Nada foi apagado: a auditoria e o desfazer dependem disso.
    assert sessao.scalar(select(func.count(Dividend.id)).where(Dividend.owner_id == dono.id)) == 7


def test_substituir_duas_vezes_nao_marca_de_novo(sessao, cenario):
    _cenario_de_substituicao(sessao, cenario)
    dono = cenario["usuario"]
    primeiro = plan_substitution(dono.id)
    pareados = {par.cb.id for par in primeiro.match.pairs}
    apply_substitution(dono.id, primeiro)

    segundo = plan_substitution(dono.id)

    assert segundo.to_supersede == [] and segundo.already_superseded == 2
    # Os créditos pareados na primeira vez estão cobertos pela linha já substituída:
    # não podem reaparecer como "CB sem linha na planilha".
    assert segundo.covered_cb == len(pareados) == 2
    assert pareados.isdisjoint({credito.id for credito in segundo.match.unmatched_cb})


def test_desfazer_devolve_as_linhas_a_planilha_ativa(sessao, cenario):
    linhas = _cenario_de_substituicao(sessao, cenario)
    dono = cenario["usuario"]
    apply_substitution(dono.id, plan_substitution(dono.id))

    assert undo_substitution(dono.id) == 2

    assert all(linha.superseded_at is None for linha in linhas.values())
    assert len(plan_substitution(dono.id).to_supersede) == 2


def test_sem_as_linhas_do_cb_no_crv_nada_e_substituido(sessao, cenario):
    """A planilha só sai quando o CB já cobre: importar vem antes de substituir."""
    _linha = _linha_planilha(cenario, "RAIZ4", "202.72", date(2026, 1, 27))
    sessao.add(_linha)
    sessao.flush()

    plano = plan_substitution(cenario["usuario"].id)

    assert plano.to_supersede == [] and [linha.id for linha in plano.divergences] == [_linha.id]
