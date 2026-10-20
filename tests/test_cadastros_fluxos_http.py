"""Ciclo de referências e carteiras, recusas e persistência por HTTP."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from test_financial_isolation_http import client_for
from test_financial_isolation_http import review_case as review_case
from test_importacao_desde_a_posicao import yahoo as yahoo
from test_riscos_http_postgres import caso as caso

from app import db
from app.models import (
    Broker,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    Portfolio,
    PortfolioTicker,
    Position,
    QuoteHistory,
    Ticker,
)

pytestmark = pytest.mark.banco


def test_corretora_nova_duplicada_editada_e_excluida(caso):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    nome = "QA " + uuid4().hex[:12]
    form = {"csrf_token": token, "name": nome, "acronym": uuid4().hex[:8]}
    identificador = None
    try:
        assert cliente.post("/tables/brokers", data=form).status_code == 302
        with app.app_context():
            registro = db.session.scalar(select(Broker).where(Broker.name == nome))
            assert registro is not None
            identificador = registro.id
        assert cliente.post("/tables/brokers", data={**form, "name": nome.lower()}).status_code == 302
        assert cliente.post(f"/tables/brokers/{identificador}", data={**form, "name": nome + " alterada"}).status_code == 302
        with app.app_context():
            assert db.session.get(Broker, identificador).name == nome + " alterada"
            assert db.session.scalar(select(db.func.count()).select_from(Broker).where(Broker.acronym == form["acronym"].upper())) == 1
        assert cliente.post(f"/tables/brokers/{identificador}/delete", data={"csrf_token": token}).status_code == 302
        with app.app_context():
            assert db.session.get(Broker, identificador) is None
    finally:
        with app.app_context():
            db.session.execute(delete(Broker).where(Broker.id == identificador))
            db.session.commit()


@pytest.mark.parametrize("campo,valor", [("symbol", ""), ("market", "inexistente"),
                                        ("rtd_market_code", "Z"), ("currency", "EUR"),
                                        ("trading_name", "")])
def test_ticker_invalido_preserva_todos_os_dados(caso, campo, valor):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    with app.app_context():
        ticker = db.session.get(Ticker, dados["ticker"])
        antes = {c.key: getattr(ticker, c.key) for c in Ticker.__table__.columns}
    form = {**antes, "csrf_token": token, campo: valor}
    assert cliente.post(f"/tables/tickers/{dados['ticker']}", data=form).status_code == 302
    with app.app_context():
        ticker = db.session.get(Ticker, dados["ticker"])
        assert {c.key: getattr(ticker, c.key) for c in Ticker.__table__.columns} == antes


def test_ticker_novo_edita_e_exclui_sem_dependencias(caso):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    simbolo = "QA" + uuid4().hex[:10].upper()
    form = {"csrf_token": token, "symbol": simbolo, "trading_name": "Ativo QA", "market": "B3",
            "currency": "BRL", "rtd_market_code": "B"}
    identificador = None
    try:
        assert cliente.post("/tables/tickers", data=form).status_code == 302
        with app.app_context():
            ticker = db.session.scalar(select(Ticker).where(Ticker.symbol == simbolo))
            assert ticker is not None
            identificador = ticker.id
        assert cliente.post(f"/tables/tickers/{identificador}", data={**form, "trading_name": "Nome atualizado"}).status_code == 302
        with app.app_context():
            assert db.session.get(Ticker, identificador).trading_name == "Nome atualizado"
        assert cliente.post(f"/tables/tickers/{identificador}/delete", data={"csrf_token": token}).status_code == 302
        with app.app_context():
            assert db.session.get(Ticker, identificador) is None
    finally:
        with app.app_context():
            db.session.execute(delete(Ticker).where(Ticker.id == identificador))
            db.session.commit()


def test_carteira_real_preserva_natureza_associacao_idempotente_e_exclusao(caso):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][0])
    nome = "QA " + uuid4().hex[:8]
    form = {"csrf_token": token, "name": nome, "currency": "BRL", "description": "Inicial"}
    assert cliente.post("/tables/portfolios", data=form).status_code == 302
    with app.app_context():
        portfolio = db.session.scalar(select(Portfolio).where(Portfolio.owner_id == dados["user_ids"][0], Portfolio.name == nome))
        assert portfolio is not None
        identificador = portfolio.id
    url = f"/tables/portfolios/{identificador}"
    assert cliente.post(url, data={**form, "name": nome + " alterada", "description": "Atualizada"}).status_code == 302
    for _ in range(2):
        assert cliente.post(url + "/tickers", data={"csrf_token": token, "ticker_id": dados["ticker"]}).status_code == 302
    with app.app_context():
        portfolio = db.session.get(Portfolio, identificador)
        assert portfolio.description == "Atualizada"
        assert db.session.scalar(select(db.func.count()).select_from(PortfolioTicker).where(PortfolioTicker.portfolio_id == identificador)) == 1
    assert cliente.post(url + f"/tickers/{dados['ticker']}/delete", data={"csrf_token": token}).status_code == 302
    assert cliente.post(url + "/delete", data={"csrf_token": token}).status_code == 302
    with app.app_context():
        assert db.session.get(Portfolio, identificador) is None


def test_carteira_com_posicao_arquiva_reativa_e_nao_perde_posicao(caso):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][2])
    url = f"/tables/portfolios/{dados['portfolio']}"
    assert cliente.post(url + "/delete", data={"csrf_token": token}).status_code == 302
    assert cliente.post(url, data={"csrf_token": token, "name": "Não gravar"}).status_code == 422
    assert cliente.post(url + "/activate", data={"csrf_token": token}).status_code == 302
    with app.app_context():
        carteira = db.session.get(Portfolio, dados["portfolio"])
        assert carteira.is_active and carteira.name == dados["names"][2]
        posicao = db.session.get(Position, dados["position"])
        opcao = db.session.get(OptionPosition, dados["option"])
        assert posicao.portfolio_id == carteira.id and posicao.quantity == Decimal("17")
        assert opcao.portfolio_id == carteira.id and opcao.quantity == Decimal("19")


def test_vencimento_e_contrato_novos_aceitam_edicao_e_exclusao(caso):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    codigo = uuid4().hex[:4].upper()
    form = {"csrf_token": token, "call_code": "Q" + codigo, "put_code": "Z" + codigo,
            "exercise_date": (date.today() + timedelta(days=10000 + int(codigo, 16))).isoformat()}
    eid = cid = None
    try:
        assert cliente.post("/tables/options/expirations", data=form).status_code == 302
        with app.app_context():
            vencimento = db.session.scalar(select(OptionExpiration).where(OptionExpiration.call_code == form["call_code"]))
            assert vencimento is not None
            eid = vencimento.id
        assert cliente.post(f"/tables/options/expirations/{eid}", data={**form, "put_code": "X" + codigo}).status_code == 302
        contrato = {"csrf_token": token, "ticker_id": dados["ticker_ids"][2], "underlying_ticker_id": dados["ticker"],
                    "expiration_id": eid, "option_type": "put", "strike": "12.34567890"}
        assert cliente.post("/tables/options/contracts", data=contrato).status_code == 302
        with app.app_context():
            registro = db.session.scalar(select(OptionContract).where(OptionContract.ticker_id == dados["ticker_ids"][2]))
            assert registro is not None and registro.strike == Decimal("12.34567890")
            cid = registro.id
        assert cliente.post(f"/tables/options/contracts/{cid}", data={**contrato, "strike": "13"}).status_code == 302
        with app.app_context():
            assert db.session.get(OptionContract, cid).strike == 13
            assert db.session.get(OptionExpiration, eid).put_code == "X" + codigo
        assert cliente.post(f"/tables/options/contracts/{cid}/delete", data={"csrf_token": token}).status_code == 302
        assert cliente.post(f"/tables/options/expirations/{eid}/delete", data={"csrf_token": token}).status_code == 302
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.execute(delete(OptionContract).where(OptionContract.id == cid))
            db.session.execute(delete(OptionExpiration).where(OptionExpiration.id == eid))
            db.session.commit()


@pytest.mark.parametrize("valor", ["0", "-1", "NaN", "Infinity"])
def test_preco_historico_invalido_nao_persiste(caso, valor):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    assert cliente.post("/quotes", data={"csrf_token": token, "ticker_id": dados["ticker"],
                                         "recorded_date": date.today().isoformat(), "price": valor}).status_code == 302
    with app.app_context():
        assert db.session.scalar(select(QuoteHistory).where(QuoteHistory.ticker_id == dados["ticker"])) is None


def test_cotacao_manual_upsert_e_exclusao_por_dia(caso):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    form = {"csrf_token": token, "ticker_id": dados["ticker"], "recorded_date": date.today().isoformat(), "price": "12.50"}
    for preco in ("12.50", "13.12345678"):
        assert cliente.post("/quotes", data={**form, "price": preco}).status_code == 302
    with app.app_context():
        linhas = db.session.scalars(select(QuoteHistory).where(QuoteHistory.ticker_id == dados["ticker"])).all()
        assert len(linhas) == 1 and linhas[0].price == Decimal("13.12345678")
    assert cliente.post("/quotes/delete-by-date", data=form).status_code == 302
    with app.app_context():
        assert db.session.scalar(select(QuoteHistory).where(QuoteHistory.ticker_id == dados["ticker"])) is None


def test_falha_do_coletor_e_recuperacao_preservam_protocolo(caso):
    from app.models import AppSetting

    app, _ = caso
    cliente = app.test_client()
    headers = {"Authorization": "Bearer " + app.config["COLLECTOR_AGENT_WRITE_TOKEN"]}
    assert cliente.post("/api/collector/failure", json={"error": "rede indisponível"}, headers=headers).status_code == 200
    with app.app_context():
        config = db.session.get(AppSetting, 1)
        assert config.collector_agent_status == "error" and config.collector_agent_error
    assert cliente.post("/api/collector/quotes", json={}, headers=headers).status_code == 200
    with app.app_context():
        config = db.session.get(AppSetting, 1)
        assert config.collector_agent_status == "online" and config.collector_agent_error is None
        assert config.collector_agent_seen_at <= datetime.now(UTC)


@pytest.mark.parametrize("caminho", ["/quotes/import", "/quotes/import-position-history"])
def test_importacao_http_persiste_parcial_nao_duplica_nem_sobrescreve_observacao_posterior(caso, yahoo, caminho):
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    dia = date.today() - timedelta(days=3)
    anterior = dia - timedelta(days=1)
    with app.app_context():
        db.session.get(Position, dados["position"]).opened_on = anterior
        # Outra referência sem série permite verificar sucesso parcial do lote.
        referencia = db.session.get(Ticker, dados["ticker_ids"][2])
        referencia.is_benchmark = True
        simbolo_falho = referencia.symbol
        simbolo = db.session.get(Ticker, dados["ticker"]).symbol
        db.session.add(QuoteHistory(ticker_id=dados["ticker"], recorded_date=dia,
                                   recorded_at=datetime.combine(dia, datetime.min.time(), tzinfo=UTC).replace(hour=21),
                                   price=Decimal("99.50")))
        db.session.commit()
    yahoo(simbolo.upper() + ".SA", [(anterior.isoformat() + "T13:00:00", 12.25),
                            (dia.isoformat() + "T13:00:00", 13.50)])
    form = {"csrf_token": token, "start_date": anterior.isoformat(), "end_date": dia.isoformat()}
    for _ in range(2):
        assert cliente.post(caminho, data=form).status_code == 302
    with app.app_context():
        historico = db.session.scalars(select(QuoteHistory).where(QuoteHistory.ticker_id == dados["ticker"])
                                      .order_by(QuoteHistory.recorded_date)).all()
        assert [(h.recorded_date, h.price) for h in historico] == [(anterior, Decimal("12.25")), (dia, Decimal("99.50"))]
        assert db.session.scalar(select(QuoteHistory).where(QuoteHistory.ticker_id == dados["ticker_ids"][2])) is None
    with cliente.session_transaction() as sessao:
        assert any(categoria == "error" and simbolo_falho in mensagem
                   for categoria, mensagem in sessao.get("_flashes", []))


@pytest.mark.parametrize("inicio,fim", [("inválida", "2026-01-01"),
                                      ("2026-01-02", "2026-01-01"),
                                      ("2026-01-01", "2999-01-01")])
def test_importacao_http_recusa_periodo_sem_acessar_provedor(caso, monkeypatch, inicio, fim):
    from app.quotes import history_import

    def nao_consultar(*args, **kwargs):
        pytest.fail("Período inválido não pode acessar o provedor de cotações.")

    monkeypatch.setattr(history_import, "urlopen", nao_consultar)
    app, dados = caso
    cliente, token = client_for(app, dados["ids"][1])
    assert cliente.post("/quotes/import", data={"csrf_token": token, "start_date": inicio, "end_date": fim}).status_code == 302
    with app.app_context():
        assert db.session.scalar(select(QuoteHistory).where(QuoteHistory.ticker_id == dados["ticker"])) is None
