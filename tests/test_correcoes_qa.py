"""Defeitos achados na rodada de testes de 21/10/2026, cada um com a sua prova.

Números fora do que a coluna guarda viravam 500; ticker de moeda diferente da
carteira era aceito; data futura entrava em posição e encerramento; o logout só
apagava o cookie; erros saíam como a página crua do Werkzeug. Cada teste abaixo
cobre um desses comportamentos.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import DataError
from test_financial_isolation_http import client_for
from test_financial_isolation_http import review_case as review_case

from app import db
from app.accounts.users import UserManagementError, create_user, update_user
from app.models import (
    Broker,
    Market,
    Position,
    PositionMovement,
    Ticker,
    Transaction,
    User,
    UserTickerEntitlement,
)

pytestmark = pytest.mark.banco


def _forma_posicao(token, dados, **extra):
    return {
        "csrf_token": token,
        "submit_token": uuid4().hex,
        "broker_id": str(dados["broker"]),
        "ticker_id": str(dados["ticker"]),
        "quantity": "10",
        "average_cost": "5",
        "side": "C",
        "opened_on": date.today().isoformat(),
        "target_multiplier": "1.5",
        "portfolio_id": str(dados["portfolio"]),
        **extra,
    }


@pytest.mark.parametrize(
    "rota,campo,valor",
    [
        ("/positions", "quantity", "1e30"),
        ("/positions", "average_cost", "1e30"),
        ("/positions", "quantity", "0.000000000001"),
        ("/transactions", "exit_price", "1e30"),
        ("/transactions", "notes", "x" * 501),
        ("/dividends", "amount", "1e30"),
        ("/dividends", "yield_on_cost", "1e30"),
        ("/options/positions", "quantity", "1e30"),
        ("/options/positions", "target_price", "1e30"),
    ],
)
def test_numero_ou_texto_fora_da_faixa_e_recusado_com_422(review_case, rota, campo, valor):
    app, dados = review_case
    cliente, token = client_for(app, dados["ids"][2])
    base = {
        "/positions": _forma_posicao(token, dados),
        "/transactions": {
            "csrf_token": token, "broker_id": str(dados["broker"]), "ticker_id": str(dados["ticker"]),
            "quantity": "10", "average_cost": "10", "exit_price": "12", "side": "C",
            "opened_on": (date.today() - timedelta(days=5)).isoformat(),
            "closed_on": date.today().isoformat(), "portfolio_id": str(dados["portfolio"]),
        },
        "/dividends": {
            "csrf_token": token, "broker_id": str(dados["broker"]), "ticker_id": str(dados["ticker"]),
            "kind": "dividendo", "amount": "5", "payment_date": date.today().isoformat(),
        },
        "/options/positions": {
            "csrf_token": token, "submit_token": uuid4().hex, "broker_id": str(dados["broker"]),
            "contract_id": str(dados["contract"]), "side": "C", "quantity": "10",
            "average_cost": "1", "opened_on": date.today().isoformat(),
            "portfolio_id": str(dados["portfolio"]),
        },
    }[rota]
    resposta = cliente.post(rota, data={**base, campo: valor})
    assert resposta.status_code == 422, (rota, campo, resposta.status_code)


def test_valor_recusado_pelo_banco_vira_422_e_nao_500(app):
    """Último recurso: se uma validação deixar passar, o banco não derruba a página."""
    erro = DataError("INSERT", {}, Exception("numeric field overflow"))
    with app.test_request_context("/positions"):
        resposta = app.handle_user_exception(erro)
    assert resposta[1] == 422
    assert "Valor não aceito" in resposta[0]


def test_ticker_de_outra_moeda_nao_entra_em_carteira_em_reais(review_case):
    app, dados = review_case
    simbolo = "QU" + uuid4().hex[:8].upper()
    with app.app_context():
        usd = Ticker(symbol=simbolo, trading_name="Ativo em dolar", market=Market.NYSE,
                     currency="USD", rtd_market_code="Y")
        db.session.add(usd)
        db.session.commit()
        usd_id = usd.id
    try:
        cliente, token = client_for(app, dados["ids"][2])
        resposta = cliente.post("/positions", data=_forma_posicao(token, dados, ticker_id=str(usd_id)))
        assert resposta.status_code == 422
        assert "cotado em USD" in resposta.get_data(as_text=True)
        with app.app_context():
            assert db.session.scalar(select(func.count()).select_from(Position).where(
                Position.ticker_id == usd_id)) == 0
    finally:
        with app.app_context():
            db.session.execute(delete(UserTickerEntitlement).where(UserTickerEntitlement.ticker_id == usd_id))
            db.session.execute(delete(Ticker).where(Ticker.id == usd_id))
            db.session.commit()


def test_data_futura_e_recusada_em_posicao_e_em_encerramento(review_case):
    app, dados = review_case
    cliente, token = client_for(app, dados["ids"][2])
    amanha = (date.today() + timedelta(days=1)).isoformat()
    resposta = cliente.post("/positions", data=_forma_posicao(token, dados, opened_on=amanha))
    assert resposta.status_code == 422
    assert "futuro" in resposta.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Position, dados["position"]).quantity == Decimal("17")
        antes = db.session.scalar(select(func.count()).select_from(Transaction).where(
            Transaction.ticker_id == dados["ticker"]))
    encerramento = cliente.post(
        f"/positions/{dados['position']}/close",
        data={"csrf_token": token, "exit_price": "30", "closed_on": amanha, "quantity": "5"},
    )
    assert encerramento.status_code == 302
    with app.app_context():
        assert db.session.get(Position, dados["position"]).quantity == Decimal("17")
        assert db.session.scalar(select(func.count()).select_from(Transaction).where(
            Transaction.ticker_id == dados["ticker"])) == antes
        assert db.session.scalar(select(func.count()).select_from(PositionMovement).where(
            PositionMovement.position_id == dados["position"],
            PositionMovement.occurred_on > date.today())) == 0


def test_logout_derruba_o_cookie_ja_emitido(review_case):
    app, dados = review_case
    identificador = dados["ids"][1]
    cliente, token = client_for(app, identificador)
    assert cliente.get("/transactions").status_code == 200
    assert cliente.post("/logout", data={"csrf_token": token}).status_code == 302

    copia, _ = client_for(app, identificador)  # o mesmo cookie, guardado antes de sair
    resposta = copia.get("/transactions")
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]
    with app.app_context():
        assert db.session.get(User, dados["user_ids"][1]).session_epoch == 1


def test_sessao_nova_depois_do_logout_continua_valida(review_case):
    app, dados = review_case
    cliente, token = client_for(app, dados["ids"][1])
    cliente.post("/logout", data={"csrf_token": token})
    with app.app_context():
        novo_id = db.session.get(User, dados["user_ids"][1]).get_id()
    outro, _ = client_for(app, novo_id)
    assert outro.get("/transactions").status_code == 200


def test_pagina_de_erro_em_portugues_com_menu_e_caminho_de_volta(review_case):
    app, dados = review_case
    cliente, _ = client_for(app, dados["ids"][2])
    resposta = cliente.get("/positions/999999")
    html = resposta.get_data(as_text=True)
    assert resposta.status_code == 404
    assert "Página não encontrada" in html
    assert "Ir para a carteira" in html
    assert "Not Found" not in html
    assert 'class="navbar"' in html


def test_erro_em_requisicao_htmx_vira_aviso_e_nao_pagina(review_case):
    app, dados = review_case
    cliente, _ = client_for(app, dados["ids"][2])
    resposta = cliente.get("/positions/999999", headers={"HX-Request": "true"})
    assert resposta.status_code == 404
    assert resposta.headers["HX-Retarget"] == "main"
    assert "data-sa-avisos" in resposta.get_data(as_text=True)


def test_erro_de_api_continua_sem_pagina(review_case, monkeypatch):
    """A superfície de máquina mantém o contrato dela (JSON/401), sem HTML do app."""
    app, _ = review_case
    monkeypatch.setitem(app.config, "COLLECTOR_AGENT_READ_TOKEN", "qa-read-" * 8)
    resposta = app.test_client().get(
        "/api/collector/configuration", headers={"Authorization": "Bearer errado"}
    )
    assert resposta.status_code == 401
    assert "Ir para" not in resposta.get_data(as_text=True)


def test_paginas_html_nao_ficam_em_cache(review_case):
    app, dados = review_case
    cliente, _ = client_for(app, dados["ids"][2])
    for caminho in ("/", "/transactions", "/positions/999999"):
        assert cliente.get(caminho).headers["Cache-Control"] == "no-store", caminho
    assert "no-store" not in cliente.get("/static/app.css").headers.get("Cache-Control", "")


@pytest.mark.parametrize(
    "caminho",
    ["/", "/options", "/transactions", "/dividends", "/dividends/new", "/positions/new",
     "/tables/brokers", "/tables/tickers", "/quotes", "/risk", "/performance", "/preferences"],
)
def test_todo_titulo_de_pagina_termina_com_o_nome_do_sistema(review_case, caminho):
    app, dados = review_case
    cliente, _ = client_for(app, dados["ids"][1])
    html = cliente.get(caminho).get_data(as_text=True)
    titulo = html.split("<title>")[1].split("</title>")[0]
    assert titulo.endswith("· Controle de Renda Variável"), titulo


def test_formulario_de_provento_usa_a_palavra_provento(review_case):
    app, dados = review_case
    cliente, _ = client_for(app, dados["ids"][2])
    html = cliente.get("/dividends/new").get_data(as_text=True)
    assert "Novo provento" in html and "Tipo de provento" in html
    assert "Nova renda" not in html


def test_casco_tem_link_de_pular_e_alvo_do_conteudo(review_case):
    app, dados = review_case
    cliente, _ = client_for(app, dados["ids"][2])
    html = cliente.get("/").get_data(as_text=True)
    assert 'href="#conteudo"' in html and 'id="conteudo"' in html
    assert html.index('class="skip-link"') < html.index('class="navbar"')
    assert '<h3 class="currency-card-title"' not in html


@pytest.mark.parametrize(
    "consulta,rotulo",
    [
        ("", "Variação não realizada"),
        ("?result_mode=proventos", "Proventos recebidos"),
        ("?result_mode=acao_proventos", "Variação + proventos"),
    ],
)
def test_rotulo_do_resumo_acompanha_o_modo_de_resultado(review_case, consulta, rotulo):
    app, dados = review_case
    cliente, _ = client_for(app, dados["ids"][2])
    html = cliente.get("/" + consulta).get_data(as_text=True)
    assert rotulo in html
    if rotulo != "Variação não realizada":
        assert "Variação não realizada" not in html


def test_corretora_arquivada_com_posicao_continua_no_filtro(review_case):
    app, dados = review_case
    cliente, token = client_for(app, dados["ids"][2])
    with app.app_context():
        nome = db.session.get(Broker, dados["broker"]).name
    assert f'<option value="{nome}"' in cliente.get("/").get_data(as_text=True)
    with app.app_context():
        db.session.get(Broker, dados["broker"]).is_active = False
        db.session.commit()
    try:
        assert f'<option value="{nome}"' in cliente.get("/").get_data(as_text=True)
    finally:
        with app.app_context():
            db.session.get(Broker, dados["broker"]).is_active = True
            db.session.commit()


def test_usuario_com_mesmo_nome_em_outra_caixa_e_recusado(review_case):
    app, dados = review_case
    with app.app_context():
        existente = db.session.get(User, dados["user_ids"][0]).username
        with pytest.raises(UserManagementError, match="já está cadastrado"):
            create_user(existente.upper(), "operador", "Senha-longa-2026", "Senha-longa-2026")
        with pytest.raises(UserManagementError, match="já está cadastrado"):
            update_user(dados["user_ids"][1], existente.upper(), "admin")
        db.session.rollback()


@pytest.mark.parametrize(
    "senha,trecho",
    [
        ("1234567890", "só números"),
        ("aaaaaaaaaaaa", "repetir"),
        ("Nome-do-usuario-1", "nome"),
    ],
)
def test_senha_fraca_e_recusada_na_criacao_de_conta(review_case, senha, trecho):
    app, _ = review_case
    with app.app_context():
        usuario = "nome-do-usuario-1" if trecho == "nome" else "qa-" + uuid4().hex[:8]
        with pytest.raises(UserManagementError, match=trecho):
            create_user(usuario, "operador", senha, senha)
        db.session.rollback()
