"""YOC e DY calculados sobre as posições quando o provento veio sem eles (regra pura)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.dividends.yields import derive_yields
from app.models import Broker, Dividend, IncomeKind, Ticker
from app.performance.dividends import build_dividend_report

D = Decimal


def _provento(identificador, valor, dia, *, cotas=None):
    linha = Dividend(
        id=identificador, owner_id=1, broker_id=1, ticker_id=7, kind=IncomeKind.DIVIDENDO,
        amount=D(valor), payment_date=dia, quotas=cotas,
    )
    linha.broker_ref = Broker(name="Genial", acronym="GNL")
    linha.ticker_ref = Ticker(symbol="BBAS3", trading_name="BB", currency="BRL")
    return linha


def test_yoc_e_dy_usam_as_cotas_do_dia_o_custo_medio_e_a_cotacao_da_data():
    dia = date(2026, 6, 11)
    linha = _provento(1, "64.01", dia)

    resultado = derive_yields(
        [linha],
        quantity_at=lambda corretora, ticker_id, quando: D("1300") if (corretora, ticker_id, quando) == ("Genial", 7, dia) else D("0"),
        average_cost={7: D("20.00")},
        price_on=lambda ticker_id, quando: D("25.00"),
    )

    (calculado,) = resultado.values()
    assert calculado.quotas == D("1300")
    # 64,01 / (1300 x 20) x 100 e 64,01 / (1300 x 25) x 100, em pontos percentuais.
    assert calculado.yield_on_cost.quantize(D("0.0001")) == D("0.2462")
    assert calculado.dividend_yield.quantize(D("0.0001")) == D("0.1970")


def test_linha_que_ja_traz_cotas_nao_e_recalculada():
    linha = _provento(1, "10.00", date(2026, 6, 11), cotas=D("100"))

    assert derive_yields([linha], quantity_at=lambda *_: D("999"), average_cost={7: D("1")}, price_on=lambda *_: D("1")) == {}


def test_sem_posicao_na_data_nada_e_calculado_e_sem_custo_ou_cotacao_o_campo_fica_vazio():
    antes_da_compra = _provento(1, "10.00", date(2026, 1, 5))
    depois_da_compra = _provento(2, "10.00", date(2026, 6, 11))

    resultado = derive_yields(
        [antes_da_compra, depois_da_compra],
        quantity_at=lambda corretora, ticker_id, quando: D("100") if quando >= date(2026, 6, 1) else D("0"),
        average_cost={},
        price_on=lambda *_: None,
    )

    assert set(resultado) == {2}
    assert resultado[2].quotas == D("100")
    assert resultado[2].yield_on_cost is None and resultado[2].dividend_yield is None


def test_relatorio_de_12_meses_usa_as_cotas_calculadas_das_linhas_do_cb():
    hoje = date(2026, 10, 18)
    linha = _provento(5, "100.00", date(2026, 9, 1))

    sem = build_dividend_report([linha], average_cost_by_ticker={7: D("10")}, quotes_by_ticker={7: D("20")}, reference_date=hoje)
    com = build_dividend_report(
        [linha], average_cost_by_ticker={7: D("10")}, quotes_by_ticker={7: D("20")}, reference_date=hoje,
        derived_quotas={5: D("500")},
    )

    assert sem.by_ticker[0].dividend_yield_12m == D("0")
    # 100 / 500 = 0,20 por cota; sobre cotação 20 e custo 10.
    assert com.by_ticker[0].dividend_yield_12m == D("0.01")
    assert com.by_ticker[0].yield_on_cost_current == D("0.02")
