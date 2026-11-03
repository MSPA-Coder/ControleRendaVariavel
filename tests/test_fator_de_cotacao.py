"""Fator de cotação e troca de código: o caso RAIZ4 → RAIZ55 (03/11/2026).

A B3 trocou o código e passou a cotar por lote de 100 ações sem mudar a
quantidade de ninguém. O que estes testes protegem é que o CRV continue por
unidade -- a cotação dividida na entrada, nada mais mexido -- e que o que
ainda chega com o código antigo ache o mesmo ticker. Cenário sintético.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.collector.database import OptionReading, persist_readings
from app.collector.rtd import QuoteValue
from app.dividends.cb_sync import plan_cb_import
from app.models import (
    AuditLog,
    Broker,
    Market,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionQuote,
    OptionType,
    Portfolio,
    Position,
    Quote,
    QuoteHistory,
    Side,
    Ticker,
    TickerFormerSymbol,
    User,
)
from app.patrimonio.cb_client import CbIncome
from app.quotes import history_import
from app.quotes.history_import import TickerImportTarget, fetch_yahoo_daily_quotes
from app.quotes.reference_data import parse_ticker
from app.quotes.symbols import (
    apply_symbol_change,
    current_symbol,
    former_symbol_map,
    per_unit,
    plan_symbol_change,
)
from tests.proventos_cb_sintetico import lancamento

D = Decimal
MUITO_DEPOIS = datetime(2027, 1, 1, tzinfo=UTC)
RAIZ55 = TickerImportTarget(6, "RAIZ55", Market.B3, quote_factor=100, first_day=date(2026, 10, 31))


# --------------------------------------------------------------------------
# Sem banco
# --------------------------------------------------------------------------


def test_preco_por_lote_vira_preco_por_unidade():
    assert per_unit(D("24.69"), 100) == D("0.2469")
    assert per_unit(D("24.69"), 1) == D("24.69")


def test_codigo_anterior_resolve_para_o_vigente_e_o_resto_fica_como_veio():
    anteriores = {"RAIZ4": "RAIZ55"}

    assert current_symbol("raiz4", anteriores) == "RAIZ55"
    assert current_symbol("PETR4", anteriores) == "PETR4"


def _formulario_de_ticker(**campos):
    base = {"symbol": "RAIZ1", "trading_name": "Raizen direito", "market": "B3", "rtd_market_code": "B", "currency": "BRL"}
    return {**base, **campos}


def test_cadastro_sem_fator_vale_um_e_aceita_o_lote_de_cem():
    assert parse_ticker(_formulario_de_ticker()).quote_factor == 1
    assert parse_ticker(_formulario_de_ticker(quote_factor="100")).quote_factor == 100


@pytest.mark.parametrize("fator", ["0", "-1", "1.5", "²", "1001", "cem"])
def test_cadastro_recusa_fator_que_nao_e_inteiro_de_1_a_1000(fator):
    with pytest.raises(ValueError, match="fator de cotação"):
        parse_ticker(_formulario_de_ticker(quote_factor=fator))


@pytest.fixture
def yahoo(monkeypatch):
    """Responde com barras de São Paulo e guarda o período que foi pedido."""
    pedidos: list[dict[str, list[str]]] = []
    barras: list[tuple[str, float]] = []

    def responder(pedido, timeout):
        pedidos.append(parse_qs(urlparse(pedido.full_url).query))
        corpo = {
            "chart": {
                "result": [
                    {
                        "meta": {"exchangeTimezoneName": "America/Sao_Paulo"},
                        "timestamp": [
                            int(datetime.fromisoformat(c).replace(tzinfo=UTC).timestamp()) for c, _ in barras
                        ],
                        "indicators": {"quote": [{"close": [f for _, f in barras]}]},
                    }
                ]
            }
        }
        return io.BytesIO(json.dumps(corpo).encode("utf-8"))

    monkeypatch.setattr(history_import, "urlopen", responder)
    return pedidos, barras


def test_yahoo_por_lote_grava_por_unidade_e_nao_pede_o_codigo_novo_antes_da_troca(yahoo):
    pedidos, barras = yahoo
    barras.extend([("2026-11-03T13:00:00", 24.69), ("2026-11-04T13:00:00", 25.10)])

    cotacoes = fetch_yahoo_daily_quotes(RAIZ55, date(2022, 5, 9), date(2026, 11, 4), now=MUITO_DEPOIS)

    assert [(c.recorded_date, c.price) for c in cotacoes] == [
        (date(2026, 11, 3), D("0.2469")),
        (date(2026, 11, 4), D("0.251")),
    ]
    inicio_pedido = datetime.fromtimestamp(int(pedidos[0]["period1"][0]), UTC).date()
    assert inicio_pedido == date(2026, 10, 31)


def test_periodo_todo_anterior_ao_codigo_novo_nao_vai_ao_yahoo(yahoo):
    pedidos, _ = yahoo

    assert fetch_yahoo_daily_quotes(RAIZ55, date(2026, 9, 1), date(2026, 10, 30), now=MUITO_DEPOIS) == []
    assert pedidos == []


# --------------------------------------------------------------------------
# Com banco
# --------------------------------------------------------------------------


@pytest.fixture
def raizen(sessao):
    """RAIZ4 com 254.000 ações e uma call sobre ela, como em 30/10/2026."""
    dono = User(username="dono-fator", password_hash="hash")
    genial = Broker(name="Genial", acronym="GNL")
    acao = Ticker(symbol="RAIZ4", trading_name="Raizen", market=Market.B3, rtd_market_code="B", currency="BRL")
    call = Ticker(symbol="RAIZH150", trading_name="RAIZ4 call", market=Market.B3, rtd_market_code="B", currency="BRL")
    sessao.add_all([dono, genial, acao, call])
    sessao.flush()
    carteira = Portfolio(owner_id=dono.id, name="BRL", currency="BRL")
    vencimento = OptionExpiration(exercise_date=date(2027, 8, 20), call_code="2027H", put_code="2027T")
    sessao.add_all([carteira, vencimento])
    sessao.flush()
    contrato = OptionContract(
        ticker_id=call.id, underlying_ticker_id=acao.id, expiration_id=vencimento.id,
        option_type=OptionType.CALL, strike=D("1.50"),
    )
    sessao.add(contrato)
    sessao.flush()
    comum = {"owner_id": dono.id, "broker_id": genial.id, "portfolio_id": carteira.id, "side": Side.BUY}
    posicao = Position(**comum, ticker_id=acao.id, quantity=D("254000"), average_cost=D("0.4626437"),
                       target_multiplier=D("1.5"), opened_on=date(2022, 5, 9))
    opcao = OptionPosition(**comum, contract_id=contrato.id, quantity=D("20000"), average_cost=D("0.1859"),
                           opened_on=date(2025, 11, 7))
    sessao.add_all([posicao, opcao])
    for dia, preco in ((date(2026, 10, 30), "0.30"), (date(2026, 11, 2), "0.30"), (date(2026, 11, 3), "0.30")):
        sessao.add(QuoteHistory(ticker_id=acao.id, price=D(preco), recorded_date=dia,
                                recorded_at=datetime.combine(dia, datetime.min.time(), UTC)))
    sessao.flush()
    return {"dono": dono, "acao": acao, "call": call, "contrato": contrato, "posicao": posicao, "opcao": opcao}


def _trocar(fator=100):
    mudanca = plan_symbol_change("RAIZ4", "RAIZ55", last_trading_day=date(2026, 10, 30), quote_factor=fator)
    apply_symbol_change(mudanca)
    return mudanca


@pytest.mark.banco
def test_troca_de_codigo_mantem_o_cadastro_e_guarda_o_codigo_anterior(sessao, raizen):
    mudanca = _trocar()
    sessao.flush()

    acao = sessao.get(Ticker, raizen["acao"].id)
    assert (acao.symbol, acao.quote_factor) == ("RAIZ55", 100)
    assert former_symbol_map() == {"RAIZ4": "RAIZ55"}
    # Quantidade e custo são por unidade e não mudam.
    posicao = sessao.get(Position, raizen["posicao"].id)
    assert (posicao.quantity, posicao.average_cost) == (D("254000"), D("0.4626437"))
    # Os dias depois do último pregão eram o coletor lendo o código morto.
    assert [dia for dia, _ in mudanca.stale_history] == [date(2026, 11, 2), date(2026, 11, 3)]
    dias = sessao.scalars(select(QuoteHistory.recorded_date).where(QuoteHistory.ticker_id == acao.id)).all()
    assert dias == [date(2026, 10, 30)]
    trilha = sessao.scalars(select(AuditLog).where(AuditLog.entity == "ticker", AuditLog.entity_id == str(acao.id))).one()
    assert trilha.details["troca_de_codigo"] == "RAIZ4 -> RAIZ55"


@pytest.mark.banco
def test_troca_de_codigo_recusa_codigo_em_uso_ou_que_ja_foi_de_outro(sessao, raizen):
    with pytest.raises(ValueError, match="já está cadastrado"):
        plan_symbol_change("RAIZ4", "RAIZH150", last_trading_day=date(2026, 10, 30), quote_factor=100)
    _trocar()
    sessao.flush()
    with pytest.raises(ValueError, match="já foi o código"):
        plan_symbol_change("RAIZH150", "RAIZ4", last_trading_day=date(2026, 10, 30), quote_factor=1)


@pytest.mark.banco
def test_coletor_grava_por_unidade_a_acao_e_o_objeto_da_opcao(sessao, raizen):
    _trocar()
    sessao.flush()
    agora = datetime(2026, 11, 3, 17, 0, tzinfo=UTC)

    persist_readings(
        [QuoteValue(raizen["posicao"].id, D("24.70"), D("30.00"), "A", agora, D("24.69"))],
        [
            OptionReading(
                option_position_id=raizen["opcao"].id,
                option=QuoteValue(-1, D("0.01"), D("0.01"), "A", agora),
                underlying_last_price=D("24.69"),
                underlying_history_price=D("24.69"),
            )
        ],
    )
    sessao.flush()

    cotacao = sessao.get(Quote, raizen["acao"].id)
    assert (cotacao.last_price, cotacao.previous_close, cotacao.buy_price) == (D("0.2469"), D("0.3"), D("0.247"))
    historico = sessao.scalars(
        select(QuoteHistory).where(QuoteHistory.ticker_id == raizen["acao"].id, QuoteHistory.recorded_date == date(2026, 11, 3))
    ).one()
    assert historico.price == D("0.2469")
    # O prêmio da opção não mudou de unidade (fator 1); o objeto, sim.
    opcao = sessao.get(OptionQuote, raizen["contrato"].id)
    assert (opcao.last_price, opcao.underlying_price) == (D("0.01"), D("0.2469"))


@pytest.mark.banco
def test_credito_do_cb_com_o_codigo_anterior_acha_o_ticker_renomeado(sessao, raizen):
    _trocar()
    sessao.flush()
    dia = date(2026, 1, 29)
    renda = CbIncome(entries=[lancamento(10, "credito", "352.56", dia, ativo="RAIZ4")])

    plano = plan_cb_import(raizen["dono"].id, renda)

    assert [linha.ticker_id for linha in plano.create] == [raizen["acao"].id]
    assert not plano.income.review


@pytest.mark.banco
def test_codigo_anterior_e_unico(sessao, raizen):
    _trocar()
    sessao.flush()
    sessao.add(TickerFormerSymbol(ticker_id=raizen["call"].id, symbol="RAIZ4", last_trading_day=date(2026, 10, 30)))
    with pytest.raises(Exception, match="uq_ticker_former_symbols_symbol"):
        sessao.flush()
