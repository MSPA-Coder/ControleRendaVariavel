"""Telas de Proventos com linhas do CB: origem, somente leitura e recusa da planilha.

Por HTTP, com sessão e CSRF reais. O cenário usa uma corretora e tickers de
nome único, e a configuração `PROVENTOS_CB_CORRETORAS` aponta para essa
corretora durante o teste, para não depender de "Genial" e "XP" do banco.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import delete, func, select

from app import db
from app.dividends.importer import REQUIRED_COLUMNS
from app.models import (
    Broker,
    Dividend,
    IncomeKind,
    Market,
    Portfolio,
    Position,
    Side,
    Ticker,
    User,
    UserTickerEntitlement,
)

pytestmark = pytest.mark.banco

D = Decimal


class _Token(HTMLParser):
    token: str | None = None

    def handle_starttag(self, tag, attrs):
        campos = dict(attrs)
        if tag == "input" and campos.get("name") == "csrf_token":
            self.token = campos.get("value")


def _cliente(app, user_id):
    client = app.test_client()
    sufixo = uuid4().hex
    client.environ_base["REMOTE_ADDR"] = "2001:db8::" + ":".join(sufixo[i : i + 4] for i in range(0, 16, 4))
    parser = _Token()
    parser.feed(client.get("/login").get_data(as_text=True))
    assert parser.token
    with client.session_transaction() as sessao:
        sessao["_user_id"] = user_id
        sessao["_fresh"] = True
    return client, parser.token


@pytest.fixture
def caso(app_com_banco, monkeypatch):
    app = app_com_banco
    sufixo = uuid4().hex[:6].upper()
    monkeypatch.setitem(app.config, "PROVENTOS_CB_CORRETORAS", f"CB{sufixo}")
    monkeypatch.setitem(app.config, "PROVENTOS_CB_CORTE", "2026-01-01")
    monkeypatch.setitem(app.config, "CB_PATRIMONIO_URL", "")
    with app.app_context():
        usuario = User(username=f"cb-{sufixo}", role="operador", is_active_user=True, must_change_password=False)
        usuario.set_password("Synthetic-review-only-2026!")
        corretora = Broker(name=f"CB{sufixo}", acronym=sufixo[:6])
        exterior = Broker(name=f"EXT{sufixo}", acronym=f"X{sufixo[:5]}")
        reais = Ticker(symbol=f"R{sufixo}", trading_name="Reais", market=Market.B3, currency="BRL", rtd_market_code="B")
        dolar = Ticker(symbol=f"D{sufixo}", trading_name="Dólar", market=Market.NASDAQ, currency="USD", rtd_market_code="Y")
        db.session.add_all([usuario, corretora, exterior, reais, dolar])
        db.session.flush()
        base = {"owner_id": usuario.id, "broker_id": corretora.id, "ticker_id": reais.id}
        linha_cb = Dividend(**base, kind=IncomeKind.ALUGUEL, amount=D("167.48"), gross_amount=D("352.56"),
                            withheld_amount=D("185.08"), payment_date=date(2026, 1, 29), origin="cb", origin_id="10")
        substituida = Dividend(**base, kind=IncomeKind.ALUGUEL, amount=D("777.77"), payment_date=date(2026, 1, 27),
                               superseded_at=func.now())
        congelada = Dividend(**base, kind=IncomeKind.DIVIDENDO, amount=D("30.00"), payment_date=date(2025, 12, 15))
        carteira = Portfolio(owner_id=usuario.id, name=f"Carteira {sufixo}", currency="BRL")
        db.session.add_all([linha_cb, substituida, congelada, carteira])
        db.session.flush()
        db.session.add(
            Position(owner_id=usuario.id, broker_id=corretora.id, ticker_id=reais.id, portfolio_id=carteira.id,
                     quantity=D("1300"), average_cost=D("20.00"), target_multiplier=1, side=Side.BUY,
                     opened_on=date(2026, 1, 10))
        )
        db.session.commit()
        ids = {
            "usuario": usuario.id, "user_id": usuario.get_id(), "corretora": corretora.id, "exterior": exterior.id,
            "reais": reais.id, "dolar": dolar.id, "cb": linha_cb.id, "substituida": substituida.id,
            "congelada": congelada.id, "ticker_reais": reais.symbol,
        }
    try:
        yield app, ids
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.execute(delete(Dividend).where(Dividend.owner_id == ids["usuario"]))
            db.session.execute(delete(Position).where(Position.owner_id == ids["usuario"]))
            db.session.execute(delete(Portfolio).where(Portfolio.owner_id == ids["usuario"]))
            db.session.execute(delete(UserTickerEntitlement).where(UserTickerEntitlement.user_id == ids["usuario"]))
            db.session.execute(delete(Ticker).where(Ticker.id.in_([ids["reais"], ids["dolar"]])))
            db.session.execute(delete(Broker).where(Broker.id.in_([ids["corretora"], ids["exterior"]])))
            db.session.execute(delete(User).where(User.id == ids["usuario"]))
            db.session.commit()


def _formulario(token, ids, *, corretora, ticker, dia, valor="25.00"):
    return {
        "csrf_token": token, "broker_id": ids[corretora], "ticker_id": ids[ticker], "amount": valor,
        "payment_date": dia, "kind": "dividendo",
    }


def _contar(app, ids):
    with app.app_context():
        return db.session.scalar(select(func.count(Dividend.id)).where(Dividend.owner_id == ids["usuario"]))


def test_formulario_recusa_provento_em_reais_da_corretora_do_cb_a_partir_do_corte(caso):
    app, ids = caso
    client, token = _cliente(app, ids["user_id"])
    antes = _contar(app, ids)

    resposta = client.post(
        "/dividends", data=_formulario(token, ids, corretora="corretora", ticker="reais", dia="2026-02-10")
    )

    assert resposta.status_code == 422
    assert "Controle Banc" in resposta.get_data(as_text=True)  # o texto vai em JSON, com acento escapado
    assert _contar(app, ids) == antes


@pytest.mark.parametrize(
    ("corretora", "ticker", "dia"),
    [
        ("corretora", "dolar", "2026-02-10"),  # dólar fica na planilha
        ("exterior", "reais", "2026-02-10"),  # corretora que o CB não cobre
        ("corretora", "reais", "2025-12-30"),  # antes do corte
    ],
)
def test_formulario_aceita_o_que_continua_na_planilha(caso, corretora, ticker, dia):
    app, ids = caso
    client, token = _cliente(app, ids["user_id"])
    antes = _contar(app, ids)

    resposta = client.post("/dividends", data=_formulario(token, ids, corretora=corretora, ticker=ticker, dia=dia))

    assert resposta.status_code == 302
    assert _contar(app, ids) == antes + 1


def test_linha_do_cb_e_somente_leitura_nas_telas(caso):
    app, ids = caso
    client, token = _cliente(app, ids["user_id"])
    caminho = f"/dividends/{ids['cb']}"

    assert client.get(f"{caminho}/edit").status_code == 403
    assert client.post(caminho, data=_formulario(token, ids, corretora="corretora", ticker="reais", dia="2025-12-01")).status_code == 403
    assert client.post(f"{caminho}/delete", data={"csrf_token": token}).status_code == 403

    with app.app_context():
        linha = db.session.get(Dividend, ids["cb"])
        assert (linha.amount, linha.payment_date, linha.origin) == (D("167.48"), date(2026, 1, 29), "cb")


def test_linha_substituida_nao_aparece_nem_se_edita_pela_tela(caso):
    app, ids = caso
    client, token = _cliente(app, ids["user_id"])

    assert client.get(f"/dividends/{ids['substituida']}/edit").status_code == 404
    assert client.post(f"/dividends/{ids['substituida']}/delete", data={"csrf_token": token}).status_code == 404

    pagina = client.get("/dividends", query_string={"expanded_tickers": ids["reais"]}).get_data(as_text=True)
    assert "777,77" not in pagina
    assert "167,48" in pagina and "352,56" in pagina  # líquido e bruto da linha do CB
    with app.app_context():
        assert db.session.get(Dividend, ids["substituida"]) is not None


def test_saldo_de_corte_soma_so_o_que_foi_pago_antes_do_corte(caso):
    app, ids = caso
    client, _ = _cliente(app, ids["user_id"])

    pagina = client.get("/dividends").get_data(as_text=True)

    assert "31/12/2025" in pagina  # subtotal "até 31/12/2025"


def test_provento_do_cb_sem_yoc_dy_e_cotas_ganha_os_calculados_sobre_a_posicao(caso):
    app, ids = caso
    client, _ = _cliente(app, ids["user_id"])

    pagina = client.get("/dividends", query_string={"expanded_tickers": ids["reais"]}).get_data(as_text=True)

    # 167,48 líquidos / (1.300 cotas x custo médio 20,00) = 0,64 pontos percentuais.
    assert "1.300" in pagina and "0,64%" in pagina


def _planilha(linhas):
    pasta = Workbook()
    folha = pasta.active
    folha.append(list(REQUIRED_COLUMNS))
    for linha in linhas:
        folha.append(linha)
    saida = BytesIO()
    pasta.save(saida)
    return saida.getvalue()


def _linha_da_planilha(ativo, corretora, dia):
    return [ativo, corretora, 50, "Dividendo", dia, dia, 1, 1, 100, 1000, 1000, 10, 10, 0.5]


def test_importacao_por_planilha_recusa_reais_do_cb_a_partir_do_corte_e_aceita_o_resto(caso):
    app, ids = caso
    with app.app_context():
        ticker_dolar = db.session.get(Ticker, ids["dolar"]).symbol
        corretora = db.session.get(Broker, ids["corretora"]).name
        exterior = db.session.get(Broker, ids["exterior"]).name
    conteudo = _planilha(
        [
            _linha_da_planilha(ids["ticker_reais"], corretora, date(2026, 2, 10)),  # recusada
            _linha_da_planilha(ticker_dolar, corretora, date(2026, 2, 10)),  # dólar: aceita
            _linha_da_planilha(ids["ticker_reais"], exterior, date(2026, 2, 10)),  # outra corretora: aceita
            _linha_da_planilha(ids["ticker_reais"], corretora, date(2025, 11, 10)),  # antes do corte: aceita
        ]
    )
    client, token = _cliente(app, ids["user_id"])
    antes = _contar(app, ids)

    resposta = client.post(
        "/dividends/import",
        data={"csrf_token": token, "workbook": (BytesIO(conteudo), "proventos.xlsx")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert resposta.status_code == 200
    assert "Controle Banc" in resposta.get_data(as_text=True)  # o texto vai em JSON, com acento escapado
    assert _contar(app, ids) == antes + 3


def test_tela_do_cb_sem_integracao_configurada_avisa_e_nao_quebra(caso):
    app, ids = caso
    client, _ = _cliente(app, ids["user_id"])

    resposta = client.get("/dividends/cb")

    assert resposta.status_code == 200
    assert "CB não configurado" in resposta.get_data(as_text=True)


def test_telas_de_proventos_do_cb_exigem_login(app_com_banco):
    assert app_com_banco.test_client().get("/dividends/cb").status_code in {302, 401, 403}
