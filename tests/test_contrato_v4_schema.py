"""O que o CRV publica em /patrimonio/v4 cumpre o JSON Schema do contrato.

O contrato v4 era descrito em três lugares (o doc do Wealthfolio, o Controle
Bancário e este repositório), e nenhum publicador testava a própria saída contra
uma definição comum: um campo renomeado ou um valor que virasse número passava na
suíte de quem mudou e quebrava o consumidor. O schema
(`tests/contrato/patrimonio-v4.schema.json`) é uma cópia da canônica em
`manutencao/docs/contratos/`; a suíte do CB e a do Wealthfolio validam contra a
mesma. Para mudar o contrato, mude a canônica e copie para todos na mesma mudança.

Aqui o schema é aplicado à saída real das três rotas, num cenário com posição
comprada, vendida e sem cotação, renda, preços atuais e históricos. E há mutações:
sem elas, um schema frouxo demais passaria sem ninguém notar.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.models import (
    Broker,
    Dividend,
    IncomeKind,
    Market,
    Portfolio,
    Position,
    Quote,
    QuoteHistory,
    Side,
    Ticker,
    User,
)

pytestmark = pytest.mark.banco

SCHEMA = json.loads((Path(__file__).parent / "contrato" / "patrimonio-v4.schema.json").read_text(encoding="utf-8"))
TOKEN_V4 = "token-de-integracao-v4-com-mais-de-trinta-e-dois-caracteres"


def validar(nome: str, instancia) -> None:
    validador = Draft202012Validator({"$ref": f"#/$defs/{nome}", "$defs": SCHEMA["$defs"]})
    erros = sorted(validador.iter_errors(instancia), key=lambda e: [str(p) for p in e.absolute_path])
    assert not erros, "\n".join(f"{'/'.join(map(str, e.absolute_path)) or '(raiz)'}: {e.message}" for e in erros[:8])


def invalido(nome: str, instancia) -> bool:
    validador = Draft202012Validator({"$ref": f"#/$defs/{nome}", "$defs": SCHEMA["$defs"]})
    return any(True for _ in validador.iter_errors(instancia))


def _ticker(simbolo, mercado=Market.B3, moeda="BRL"):
    return Ticker(symbol=simbolo, trading_name=f"{simbolo} S.A.", market=mercado, rtd_market_code="B", currency=moeda)


@pytest.fixture
def cenario(sessao):
    usuario = User(username="dono", password_hash="hash")
    corretora = Broker(name="Genial", acronym="GNL")
    comprada = _ticker("WEGE3")
    vendida = _ticker("PETR4")
    sem_cotacao = _ticker("SEMQ3")
    americana = _ticker("AAPL", Market.NASDAQ, "USD")
    real = Portfolio(name="BRL", owner_ref=usuario, currency="BRL", simulated=False)
    simulada = Portfolio(name="Simulada", owner_ref=usuario, currency="BRL", simulated=True)
    sessao.add_all([usuario, corretora, comprada, vendida, sem_cotacao, americana, real, simulada])
    sessao.flush()
    agora = datetime.now(UTC)
    sessao.add_all(
        [
            Quote(ticker_id=comprada.id, last_price=Decimal("52.10"), previous_close=Decimal("51.00"),
                  source_status="online", observed_at=agora),
            Quote(ticker_id=vendida.id, last_price=Decimal("38.45"), previous_close=Decimal("38.00"),
                  source_status="online", observed_at=agora),
            Quote(ticker_id=americana.id, last_price=Decimal("215.30"), previous_close=Decimal("214.10"),
                  source_status="online", observed_at=agora),
        ]
    )
    for dias in (1, 2, 3):
        dia = date.today() - timedelta(days=dias)
        sessao.add(QuoteHistory(ticker_id=comprada.id, price=Decimal("50") + dias, recorded_date=dia,
                                recorded_at=datetime.combine(dia, datetime.min.time(), tzinfo=UTC)))
    for ticker, lado, quantidade, custo, carteira in (
        (comprada, Side.BUY, "300", "40.00", real),
        (vendida, Side.SELL, "100", "35.5", real),
        (sem_cotacao, Side.BUY, "10", "5.00", real),
        (americana, Side.BUY, "2.5", "180.1234", real),
        (comprada, Side.BUY, "1000", "40.00", simulada),
    ):
        sessao.add(Position(
            broker_id=corretora.id, ticker_id=ticker.id, portfolio_id=carteira.id, owner_id=usuario.id,
            quantity=Decimal(quantidade), average_cost=Decimal(custo), side=lado, opened_on=date(2026, 1, 10),
        ))
    sessao.add(Dividend(owner_id=usuario.id, kind=IncomeKind.JCP, broker_id=corretora.id, ticker_id=comprada.id,
                        amount=Decimal("42.50"), payment_date=date(2026, 9, 15)))
    sessao.flush()
    return {"usuario": usuario}


@pytest.fixture
def publicando(app_com_banco, cenario):
    app_com_banco.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4
    app_com_banco.config["PATRIMONIO_TITULAR"] = "Mariano"
    app_com_banco.config["PATRIMONIO_OWNER_ID"] = str(cenario["usuario"].id)
    return app_com_banco.test_client()


def pedir(publicando, caminho: str, **parametros):
    resposta = publicando.get(caminho, query_string=parametros, headers={"Authorization": f"Bearer {TOKEN_V4}"})
    assert resposta.status_code == 200, resposta.get_data(as_text=True)[:300]
    return resposta.get_json()


@pytest.fixture
def snapshot(publicando):
    return pedir(publicando, "/patrimonio/v4/snapshot")


def test_o_proprio_schema_e_um_schema_valido():
    Draft202012Validator.check_schema(SCHEMA)


def test_metadata_cumpre_o_contrato(publicando):
    validar("crv_metadata", pedir(publicando, "/patrimonio/v4/metadata"))


def test_snapshot_cumpre_o_contrato_com_todos_os_recursos(snapshot):
    validar("crv_snapshot", snapshot)
    assert len(snapshot["holdings"]) == 4  # a simulada fica de fora
    assert snapshot["income"] and snapshot["prices"]["current"] and snapshot["prices"]["history"]
    assert snapshot["accounts"]


def test_posicao_sem_cotacao_e_vendida_cumprem_o_contrato(snapshot):
    por_ativo = {h["instrument"]: h for h in snapshot["holdings"]}
    assert por_ativo["SEMQ3"]["current_price"] is None and por_ativo["SEMQ3"]["market_value"] is None
    assert por_ativo["PETR4"]["side"] == "short" and por_ativo["PETR4"]["market_value"].startswith("-")
    assert por_ativo["AAPL"]["currency"] == "USD" and por_ativo["AAPL"]["market"] == "NASDAQ"
    validar("crv_snapshot", snapshot)


def test_snapshot_sem_precos_continua_valido(publicando):
    corpo = pedir(publicando, "/patrimonio/v4/snapshot", include_prices="false")
    validar("crv_snapshot", corpo)
    assert corpo["coverage"]["prices"]["omitted"] == "consumer_requested"


def test_changes_cumpre_o_contrato(publicando):
    corpo = pedir(publicando, "/patrimonio/v4/changes", limit=500)
    validar("crv_changes", corpo)
    assert corpo["items"], "o cenário grava pela aplicação, então a outbox tem itens"


def _primeiro(lista):
    return lista[0]


def _holding_sem_instrument_type(s):
    del _primeiro(s["holdings"])["instrument_type"]


def _instrument_type_que_o_consumidor_rejeita(s):
    _primeiro(s["holdings"])["instrument_type"] = "option"


def _quantidade_vira_numero(s):
    _primeiro(s["holdings"])["quantity"] = 300


def _valor_de_mercado_vira_float(s):
    _primeiro(s["holdings"])["market_value"] = 15630.0


def _lado_fora_do_vocabulario(s):
    _primeiro(s["holdings"])["side"] = "C"


def _id_sem_prefixo_da_fonte(s):
    _primeiro(s["holdings"])["source_id"] = "holding:1"


def _campo_renomeado(s):
    holding = _primeiro(s["holdings"])
    holding["instrumento"] = holding.pop("instrument")


def _renda_sem_papel_analitico(s):
    _primeiro(s["income"])["role"] = "cash_entry"


def _data_fora_do_iso(s):
    _primeiro(s["income"])["date"] = "15/09/2026"


def _preco_historico_sem_data(s):
    del _primeiro(s["prices"]["history"])["date"]


def _sistema_errado(s):
    s["sistema"] = "controle-bancario"


def _sem_cobertura_de_posicoes(s):
    del s["coverage"]["holdings"]["complete"]


def _sem_high_watermark(s):
    del s["high_watermark"]


@pytest.mark.parametrize(
    "mutacao",
    [
        _holding_sem_instrument_type, _instrument_type_que_o_consumidor_rejeita, _quantidade_vira_numero,
        _valor_de_mercado_vira_float, _lado_fora_do_vocabulario, _id_sem_prefixo_da_fonte, _campo_renomeado,
        _renda_sem_papel_analitico, _data_fora_do_iso, _preco_historico_sem_data, _sistema_errado,
        _sem_cobertura_de_posicoes, _sem_high_watermark,
    ],
    ids=lambda f: f.__name__.strip("_"),
)
def test_o_schema_reprova_cada_mutacao_do_snapshot(snapshot, mutacao):
    """Sem isto, um schema frouxo demais passaria sem ninguém notar."""
    quebrado = copy.deepcopy(snapshot)
    mutacao(quebrado)

    assert invalido("crv_snapshot", quebrado), mutacao.__name__


def test_o_schema_aceita_um_acrescimo_opcional(snapshot):
    """Campo a mais não muda a versão do contrato: quem não o conhece o ignora."""
    novo = copy.deepcopy(snapshot)
    _primeiro(novo["holdings"])["campo_novo_e_opcional"] = "x"
    novo["extra_na_raiz"] = True

    validar("crv_snapshot", novo)
