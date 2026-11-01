"""Conferência do exterior contra o PostgreSQL: negócio a negócio pelo extrato de
posição do CRV, e a carteira do extrato da corretora (publicada pelo CB) contra a
posição do CRV no último pregão já liquidado.

Cenário com a forma da Avenue de 2026, em números sintéticos: uma posição
encerrada antes do arquivo de movimentos (só quantidades), uma viva com extrato e
preço, e uma compra no último pregão do mês, que só liquida no mês seguinte.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.executions.conferencia import (
    AWAITING_STATEMENT,
    DIFFERENCE,
    MATCH,
    ONLY_CB,
    build_reconciliation,
)
from app.models import (
    Broker,
    Market,
    Portfolio,
    Position,
    PositionLedgerArchive,
    PositionMovement,
    PositionMovementKind,
    Side,
    Ticker,
    User,
)
from app.patrimonio.cb_client import CbConfig
from tests.test_cb_client import CATEGORIA_BOLSA, _categoria, _lancamento, _snapshot

pytestmark = pytest.mark.banco

D = Decimal
CONTA = "controle-bancario:account:usd"


@pytest.fixture
def cenario(sessao, app_com_banco, monkeypatch):
    sufixo = uuid4().hex[:8]
    dono = User(username=f"exterior-{sufixo}", password_hash="hash")
    outro = User(username=f"exterior-outro-{sufixo}", password_hash="hash")
    corretora = Broker(name=f"Avenue {sufixo}", acronym=sufixo[:6])
    cgc = Ticker(symbol=f"C{sufixo[:5].upper()}", trading_name="Canopy", market=Market.NASDAQ,
                 rtd_market_code="N", currency="USD")
    brk = Ticker(symbol=f"B{sufixo[:4].upper()}.B", trading_name="Berkshire", market=Market.NYSE,
                 rtd_market_code="Y", currency="USD")
    sessao.add_all([dono, outro, corretora, cgc, brk])
    sessao.flush()
    carteira = Portfolio(name="USD", owner_ref=dono, currency="USD")
    do_outro = Portfolio(name="USD", owner_ref=outro, currency="USD")
    sessao.add_all([carteira, do_outro])
    sessao.flush()

    # Encerrada em abril, antes do arquivo de movimentos: o CRV só sabe as quantidades.
    for dia, quantidade in ((date(2026, 2, 11), "1200"), (date(2026, 3, 20), "2257"), (date(2026, 4, 22), "0")):
        sessao.add(PositionLedgerArchive(
            owner_id=dono.id, occurred_on=dia, ticker_id=cgc.id, portfolio_id=carteira.id, broker_id=corretora.id,
            instrument="stock", source_position_id=10_000 + int(sufixo[:4], 16), resulting_signed_quantity=D(quantidade),
        ))
    # Viva, com extrato e preço: abre em 16/06, reforça em 23/07 e no último pregão de setembro.
    viva = Position(owner_id=dono.id, broker_id=corretora.id, ticker_id=cgc.id, portfolio_id=carteira.id,
                    quantity=D("9808"), average_cost=D("0.92"), side=Side.BUY, opened_on=date(2026, 6, 16))
    brk_viva = Position(owner_id=dono.id, broker_id=corretora.id, ticker_id=brk.id, portfolio_id=carteira.id,
                        quantity=D("1.56365"), average_cost=D("476.575"), side=Side.BUY, opened_on=date(2025, 7, 31))
    # Outro dono, mesma corretora: não pode vazar.
    alheia = Position(owner_id=outro.id, broker_id=corretora.id, ticker_id=cgc.id, portfolio_id=do_outro.id,
                      quantity=D("999"), average_cost=D("1"), side=Side.BUY, opened_on=date(2026, 6, 16))
    sessao.add_all([viva, brk_viva, alheia])
    sessao.flush()
    extrato = [
        (viva, PositionMovementKind.OPEN, "5000", "0.95", date(2026, 6, 16), "5000"),
        (viva, PositionMovementKind.INCREASE, "2500", "0.90", date(2026, 7, 23), "7500"),
        (viva, PositionMovementKind.INCREASE, "2308", "0.90", date(2026, 9, 30), "9808"),
        (brk_viva, PositionMovementKind.OPEN, "1.56365", "476.575", date(2025, 7, 31), "1.56365"),
        (alheia, PositionMovementKind.OPEN, "999", "1", date(2026, 6, 16), "999"),
    ]
    for posicao, tipo, delta, preco, dia, resultante in extrato:
        sessao.add(PositionMovement(
            owner_id=posicao.owner_id, position_id=posicao.id, kind=tipo, quantity_delta=D(delta), price=D(preco),
            occurred_on=dia, resulting_quantity=D(resultante), resulting_average_cost=D(preco),
        ))
    sessao.flush()
    monkeypatch.setitem(app_com_banco.config, "CONFERENCIA_CORRETORAS_EXTERIOR", corretora.name)
    monkeypatch.setitem(app_com_banco.config, "CB_CORRESPONDENCIA_CONTAS", json.dumps({corretora.name: ["avenue"]}))
    return dono, corretora, cgc, brk


def _cb(cgc, brk, *, compra_de_julho="2500"):
    conta = {
        "resource": "account", "source_id": CONTA,
        "payload": {
            "source_id": CONTA, "name": "Conta em dólar", "institution": "avenue", "institution_name": "Avenue",
            "currency": "USD", "statement_covered_until": "2026-09-30",
            "statement_holdings": [
                # Como em janeiro de 2026, o extrato imprime o símbolo sem o ponto.
                {"date": "2026-02-28", "positions": [{"instrument": brk.symbol.replace(".", ""), "quantity": "1.56365"},
                                                     {"instrument": cgc.symbol, "quantity": "1200"}]},
                {"date": "2026-04-30", "positions": [{"instrument": brk.symbol, "quantity": "1.56365"}]},
                {"date": "2026-09-30", "positions": [{"instrument": brk.symbol, "quantity": "1.56365"},
                                                     {"instrument": cgc.symbol, "quantity": "7500"}]},
            ],
        },
    }
    itens = [conta, _categoria(CATEGORIA_BOLSA, "Operações em Bolsa")]
    negocios = [
        ("fev", date(2026, 2, 11), "1260.00", "despesa", "1200"),
        ("mar", date(2026, 3, 20), "1057.00", "despesa", "1057"),
        ("abr", date(2026, 4, 22), "3047.40", "receita", "2257"),
        ("jun", date(2026, 6, 16), "4750.00", "despesa", "5000"),
        ("jul", date(2026, 7, 23), "2250.00", "despesa", compra_de_julho),
        ("ago", date(2026, 8, 5), "10.00", "despesa", "10"),
    ]
    for chave, pregao, valor, tipo, quantidade in negocios:
        lancamento = _lancamento(chave, CONTA, CATEGORIA_BOLSA, f"Negócio {chave}", valor, tipo=tipo,
                                 instrument=cgc.symbol, settlement={"pregao_date": pregao.isoformat(), "quantity": quantidade})
        lancamento["payload"]["currency"] = "USD"
        itens.append(lancamento)
    return _snapshot(*itens)


def _conferir(dono, fetcher):
    return build_reconciliation(dono.id, fetcher=fetcher, config_factory=lambda: CbConfig("http://cb.test", "t"))


def test_cada_negocio_da_avenue_e_conferido_pelo_extrato_de_posicao(sessao, cenario):
    dono, corretora, cgc, brk = cenario

    resultado = _conferir(dono, _cb(cgc, brk))

    exterior = {linha.pregao: linha for linha in resultado.rows if linha.broker == corretora.name}
    assert resultado.cb_status == "ok"
    # Encerrada antes do arquivo: confere pela quantidade, sem valor do CRV.
    assert (exterior[date(2026, 2, 11)].state, exterior[date(2026, 2, 11)].crv_amount) == (MATCH, None)
    assert exterior[date(2026, 4, 22)].crv_quantity == D("-2257")
    assert exterior[date(2026, 4, 22)].state == MATCH
    # Viva com preço: quantidade e valor.
    assert exterior[date(2026, 6, 16)].crv_amount == D("-4750.0000")
    assert exterior[date(2026, 6, 16)].crv_quantity == D("5000"), "a posição de outro dono vazou"
    assert exterior[date(2026, 6, 16)].state == MATCH
    assert exterior[date(2026, 7, 23)].state == MATCH
    assert exterior[date(2026, 8, 5)].state == ONLY_CB
    # Último pregão de setembro: liquida em outubro, depois do extrato importado.
    assert exterior[date(2026, 9, 30)].state == AWAITING_STATEMENT
    # Antes do início do CB (01/01/2026) não há o que conferir.
    assert date(2025, 7, 31) not in exterior


def test_quantidade_errada_no_crv_aparece_como_diferenca(sessao, cenario):
    dono, corretora, cgc, brk = cenario

    resultado = _conferir(dono, _cb(cgc, brk, compra_de_julho="2600"))

    julho = next(linha for linha in resultado.rows if linha.pregao == date(2026, 7, 23))
    assert (julho.state, julho.cb_quantity, julho.crv_quantity) == (DIFFERENCE, D("2600"), D("2500"))


def test_carteira_do_extrato_confere_com_a_posicao_liquidada_do_crv(sessao, cenario):
    """Em 30/09 o extrato mostra 7.500 CGC: a compra de 30/09 só liquida em outubro."""
    dono, corretora, cgc, brk = cenario

    resultado = _conferir(dono, _cb(cgc, brk))

    carteira = {(linha.statement_date, linha.instrument): linha for linha in resultado.holdings}
    assert carteira[(date(2026, 9, 30), cgc.symbol)].crv_quantity == D("7500")
    assert carteira[(date(2026, 9, 30), cgc.symbol)].position_date == date(2026, 9, 29)
    assert carteira[(date(2026, 2, 28), brk.symbol)].matches, "o símbolo sem ponto do extrato é o do CRV"
    assert all(linha.matches for linha in resultado.holdings)
    assert {linha.broker for linha in resultado.holdings} == {corretora.name}


def test_carteira_do_extrato_acusa_o_que_o_crv_nao_tem(sessao, cenario):
    dono, _, cgc, brk = cenario
    fetcher = _cb(cgc, brk)

    def com_cgc_a_mais(url, token):
        documento = fetcher(url, token)
        documento["items"][0]["payload"]["statement_holdings"][1]["positions"].append(
            {"instrument": cgc.symbol, "quantity": "100"}
        )
        return documento

    resultado = _conferir(dono, com_cgc_a_mais)

    divergentes = [(linha.statement_date, linha.statement_quantity, linha.crv_quantity)
                   for linha in resultado.holdings if not linha.matches]
    assert divergentes == [(date(2026, 4, 30), D("100"), D("0"))]
