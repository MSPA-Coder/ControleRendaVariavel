"""Configurações, referências, opções e lotes do coletor pelo HTTP real."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from test_financial_isolation_http import client_for
from test_financial_isolation_http import review_case as review_case

from app import db
from app.models import (
    AppSetting,
    Broker,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionQuote,
    Quote,
    QuoteHistory,
    Ticker,
    UserPreference,
)

pytestmark = pytest.mark.banco


@pytest.fixture
def caso(review_case, monkeypatch):
    app, dados = review_case
    monkeypatch.setitem(app.config, "COLLECTOR_AGENT_WRITE_TOKEN", "qa-write-" * 8)
    monkeypatch.setitem(app.config, "COLLECTOR_AGENT_READ_TOKEN", "qa-read-" * 8)
    with app.app_context():
        atual = db.session.get(AppSetting, 1)
        anterior = {c.key: getattr(atual, c.key) for c in AppSetting.__table__.columns} if atual else None
    yield app, dados
    with app.app_context():
        db.session.rollback()
        for model in (Quote, QuoteHistory):
            db.session.execute(delete(model).where(model.ticker_id.in_(dados["ticker_ids"])))
        db.session.execute(delete(OptionQuote).where(OptionQuote.contract_id == dados["contract"]))
        atual = db.session.get(AppSetting, 1)
        if anterior:
            for key, value in anterior.items():
                setattr(atual, key, value)
        elif atual:
            db.session.delete(atual)
        db.session.commit()


def formulario_config(token):
    return {"csrf_token": token, "poll_interval_seconds": "3", "agent_check_interval_seconds": "20",
            "collector_schedule_weekdays": ["0", "2", "4"], "collector_schedule_start_time": "10:00",
            "collector_schedule_end_time": "17:00", "risk_free_rate_annual": "0.12",
            "stale_alert_seconds": "45", "benchmark_ticker_id": ""}


@pytest.mark.parametrize("campo,valor", [
    ("poll_interval_seconds", "0"), ("agent_check_interval_seconds", "4"),
    ("collector_schedule_weekdays", ["9"]), ("collector_schedule_start_time", "23:00"),
    ("risk_free_rate_annual", "NaN"), ("stale_alert_seconds", "86401"),
    ("stale_alert_seconds", "abc"), ("benchmark_ticker_id", "2147483647"),
])
def test_configuracao_invalida_reverte_todos_os_campos(caso, campo, valor):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    assert cliente.post("/settings", data=formulario_config(token)).status_code == 302
    with app.app_context():
        antes = db.session.get(AppSetting, 1).poll_interval_seconds
    formulario = {**formulario_config(token), "poll_interval_seconds": "8", campo: valor}
    assert cliente.post("/settings", data=formulario).status_code == 422
    with app.app_context():
        assert db.session.get(AppSetting, 1).poll_interval_seconds == antes
        assert db.session.get(UserPreference, dados["user_ids"][1]).risk_free_rate_annual == Decimal("0.12")


def test_configuracao_persiste_agenda_e_pedido_de_refresh(caso):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    assert cliente.post("/settings", data=formulario_config(token)).status_code == 302
    assert cliente.post("/settings/collector/refresh", data={"csrf_token": token}).status_code == 302
    with app.app_context():
        config = db.session.get(AppSetting, 1)
        assert config.collector_schedule_weekdays == "0,2,4"
        assert config.collector_refresh_requested_at
        assert config.stale_alert_seconds == 45
    operador, token_operador = client_for(app, dados["ids"][0])
    assert operador.post("/settings", data=formulario_config(token_operador)).status_code == 403


@pytest.mark.parametrize("recurso,modelo,chave", [("brokers", Broker, "broker"), ("tickers", Ticker, "ticker")])
def test_referencia_em_uso_arquiva_recusa_edicao_e_reativa(caso, recurso, modelo, chave):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    url = f"/tables/{recurso}/{dados[chave]}"
    with app.app_context():
        registro = db.session.get(modelo, dados[chave])
        nome = registro.name if modelo is Broker else registro.symbol
    assert cliente.post(url + "/delete", data={"csrf_token": token}).status_code == 302
    assert cliente.post(url, data={"csrf_token": token, "name": "Não alterar", "symbol": "NAO"}).status_code == 302
    with app.app_context():
        registro = db.session.get(modelo, dados[chave])
        assert registro.is_active is False
        assert (registro.name if modelo is Broker else registro.symbol) == nome
        assert db.session.get(OptionPosition, dados["option"]).quantity == 19
    assert cliente.post(url + "/activate", data={"csrf_token": token}).status_code == 302
    with app.app_context():
        assert db.session.get(modelo, dados[chave]).is_active


def test_referencias_de_opcao_em_uso_nao_sao_excluidas(caso):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    for recurso, chave, modelo in [("contracts", "contract", OptionContract), ("expirations", "expiration", OptionExpiration)]:
        assert cliente.post(f"/tables/options/{recurso}/{dados[chave]}/delete", data={"csrf_token": token}).status_code == 302
        with app.app_context():
            assert db.session.get(modelo, dados[chave]) is not None


@pytest.mark.parametrize("campo,valor", [("strike", "NaN"), ("strike", "-0.01"), ("underlying_ticker_id", "mesmo")])
def test_contrato_invalido_nao_modifica_strike_nem_relacoes(caso, campo, valor):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    form = {"csrf_token": token, "ticker_id": dados["ticker_ids"][1], "underlying_ticker_id": dados["ticker"],
            "expiration_id": dados["expiration"], "option_type": "call", "strike": "17"}
    form[campo] = form["ticker_id"] if valor == "mesmo" else valor
    assert cliente.post(f"/tables/options/contracts/{dados['contract']}", data=form).status_code == 302
    with app.app_context():
        contrato = db.session.get(OptionContract, dados["contract"])
        assert contrato.strike == 10
        assert contrato.underlying_ticker_id == dados["ticker"]


@pytest.mark.parametrize("quantidade,preco", [("20", "3"), ("0", "3"), ("NaN", "3"), ("1", "-1")])
def test_encerramento_invalido_preserva_saldo_opcao(caso, quantidade, preco):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][2])
    response = cliente.post(f"/options/positions/{dados['option']}/close", data={
        "csrf_token": token, "quantity": quantidade, "exit_price": preco, "closed_on": date.today().isoformat(),
    })
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(OptionPosition, dados["option"]).quantity == 19


def test_opcao_http_reenvio_nao_duplica_aporte_e_ajuste_persiste(caso):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][0])
    form = {"csrf_token": token, "submit_token": uuid4().hex, "broker_id": dados["broker"],
            "contract_id": dados["contract"], "portfolio_id": dados["portfolio_ids"][0],
            "quantity": "4", "average_cost": "1.5", "target_price": "2", "side": "C",
            "opened_on": date.today().isoformat()}
    for _ in range(2):
        assert cliente.post("/options/positions", data=form).status_code == 302
    with app.app_context():
        posicao = db.session.scalar(select(OptionPosition).where(OptionPosition.owner_id == dados["user_ids"][0]))
        assert posicao.quantity == 4
        identificador = posicao.id
        versao = posicao.edit_version
    assert cliente.post(f"/options/positions/{identificador}", data={**form, "edit_version": str(versao), "target_price": "2.5"}).status_code == 302
    with app.app_context():
        assert db.session.get(OptionPosition, identificador).target_price == Decimal("2.5")
    assert cliente.post(f"/options/positions/{identificador}/delete", data={"csrf_token": token}).status_code == 302
    with app.app_context():
        assert db.session.get(OptionPosition, identificador) is None


def leitura(dados, instante, preco="23"):
    return {"position_id": dados["position"], "last_price": preco, "previous_close": "20",
            "history_price": preco, "instrument_status": "Aberto", "observed_at": instante.isoformat()}


def test_coleta_fora_de_ordem_e_lote_invalido_preservam_ultima_cotacao(caso):
    app, dados = caso
    cliente = app.test_client()
    headers = {"Authorization": "Bearer " + app.config["COLLECTOR_AGENT_WRITE_TOKEN"]}
    agora = datetime.now(UTC)
    assert cliente.post("/api/collector/quotes", json={"positions": [leitura(dados, agora)]}, headers=headers).status_code == 200
    assert cliente.post("/api/collector/quotes", json={"positions": [leitura(dados, agora - timedelta(minutes=1), "1")]}, headers=headers).status_code == 200
    invalida = {**leitura(dados, agora), "position_id": 2147483647}
    assert cliente.post("/api/collector/quotes", json={"positions": [leitura(dados, agora + timedelta(seconds=1), "99"), invalida]}, headers=headers).status_code == 400
    with app.app_context():
        quote = db.session.scalar(select(Quote).where(Quote.ticker_id == dados["ticker"]))
        assert quote.last_price == 23
        assert db.session.get(AppSetting, 1).collector_agent_status == "online"


@pytest.mark.parametrize("corpo", [{"positions": {}}, {"positions": [None]}, {"positions": [{}]}, {"positions": [{}] * 2001}])
def test_coleta_malformada_recusada_sem_gravar(caso, corpo):
    app, dados = caso
    headers = {"Authorization": "Bearer " + app.config["COLLECTOR_AGENT_WRITE_TOKEN"]}
    assert app.test_client().post("/api/collector/quotes", json=corpo, headers=headers).status_code == 400
    with app.app_context():
        assert db.session.scalar(select(Quote).where(Quote.ticker_id == dados["ticker"])) is None
