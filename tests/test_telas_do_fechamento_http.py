"""As telas que tiraram o SSH do fechamento: coleta do myProfit e aplicar negócios.

Por HTTP, com sessão e CSRF reais, contra o `db-teste`. As duas chamam o mesmo
caso de uso do comando (`coleta_importacao`, `apply_executions`); o que se mede
aqui é o que só a tela tem: o ensaio não grava, gravar exige o que o ensaio
mostrou, e sem login ou CSRF nada acontece.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app import db
from app.models import (
    Broker,
    Execution,
    Market,
    MyprofitColeta,
    MyprofitPosition,
    Portfolio,
    Position,
    Ticker,
    User,
)
from tests.myprofit_sintetico import CPF_FICTICIO, TITULAR_FICTICIO, linha
from tests.test_fechamento_status import _pacote, _tsv_dos_negocios
from tests.test_financial_isolation_http import client_for

pytestmark = pytest.mark.banco

D = Decimal


@pytest.fixture
def caso(app_com_banco, monkeypatch):
    app = app_com_banco
    sufixo = uuid4().hex[:6].upper()
    with app.app_context():
        usuario = User(username=f"tela-{sufixo.lower()}", role="operador", is_active_user=True, must_change_password=False)
        usuario.set_password("Synthetic-screens-only-2026!")
        corretora = Broker(name=f"Genial {sufixo}", acronym=sufixo[:6])
        acao = Ticker(symbol=f"TELA{sufixo}", trading_name="Ação", market=Market.B3, rtd_market_code="B", currency="BRL")
        db.session.add_all([usuario, corretora, acao])
        db.session.flush()
        db.session.add(Portfolio(name=f"Real {sufixo}", owner_id=usuario.id, currency="BRL"))
        db.session.commit()
        ids = {
            "usuario": usuario.id, "sessao": usuario.get_id(), "corretora": corretora.id, "acao": acao.id,
            "simbolo": acao.symbol, "sufixo": sufixo,
        }
    monkeypatch.setitem(app.config, "MYPROFIT_INSTITUICOES", json.dumps({"GENIAL": f"Genial {sufixo}"}))
    monkeypatch.setitem(app.config, "CB_PATRIMONIO_URL", "")
    try:
        yield app, ids
    finally:
        with app.app_context():
            db.session.rollback()
            # A aplicação cria posição, movimento e o que mais o serviço gravar:
            # apaga-se tudo o que é do dono, dos filhos para os pais.
            for tabela in reversed(db.metadata.sorted_tables):
                if "owner_id" in tabela.c:
                    db.session.execute(delete(tabela).where(tabela.c.owner_id == ids["usuario"]))
                elif "user_id" in tabela.c:
                    db.session.execute(delete(tabela).where(tabela.c.user_id == ids["usuario"]))
            db.session.execute(delete(Ticker).where(Ticker.id == ids["acao"]))
            db.session.execute(delete(Broker).where(Broker.id == ids["corretora"]))
            db.session.execute(delete(User).where(User.id == ids["usuario"]))
            db.session.commit()


def _coleta(ids) -> bytes:
    negocios = _tsv_dos_negocios([linha(date(2026, 10, 15), "NC-1", ids["simbolo"], 10, "-101.50")])
    return _pacote(ids["sufixo"], negocios=negocios)


def _enviar_coleta(client, token, conteudo, acao, **extra):
    return client.post(
        "/executions/collection",
        data={"csrf_token": token, "action": acao, "pacote": (BytesIO(conteudo), "coleta.json"), **extra},
        content_type="multipart/form-data",
    )


def _contar(app, modelo, usuario_id) -> int:
    with app.app_context():
        return db.session.scalar(select(func.count()).select_from(modelo).where(modelo.owner_id == usuario_id))


def _campo(html: str, nome: str) -> str:
    achado = re.search(rf'name="{nome}" value="([^"]*)"', html)
    assert achado, f"a tela não trouxe o campo {nome}"
    return achado.group(1)


# --- Coleta do myProfit -------------------------------------------------------------

def test_coleta_pela_tela_ensaia_sem_gravar_e_grava_so_o_mesmo_pacote(caso):
    app, ids = caso
    client, token = client_for(app, ids["sessao"])
    conteudo = _coleta(ids)

    assert client.get("/executions/collection").status_code == 200
    ensaio = _enviar_coleta(client, token, conteudo, "ensaio")
    html = ensaio.get_data(as_text=True)
    assert ensaio.status_code == 200
    assert "Gravar a coleta" in html
    assert TITULAR_FICTICIO not in html and CPF_FICTICIO not in html
    assert _contar(app, Execution, ids["usuario"]) == 0
    assert _contar(app, MyprofitColeta, ids["usuario"]) == 0
    sha = _campo(html, "expected_sha256")

    outro = _pacote(ids["sufixo"], ate="2026-10-21")
    recusado = _enviar_coleta(client, token, outro, "gravar", expected_sha256=sha)
    assert recusado.status_code == 422
    assert _contar(app, MyprofitColeta, ids["usuario"]) == 0

    gravado = _enviar_coleta(client, token, conteudo, "gravar", expected_sha256=sha)
    assert gravado.status_code == 302 and gravado.headers["Location"].endswith("/executions/apply")
    assert _contar(app, Execution, ids["usuario"]) == 1
    assert _contar(app, MyprofitColeta, ids["usuario"]) == 1
    assert _contar(app, MyprofitPosition, ids["usuario"]) == 4  # três linhas em 30/09 e uma em 20/10

    # Reimportar é seguro: o negócio repetido é reconhecido e a coleta do mesmo pacote é a mesma.
    de_novo = _enviar_coleta(client, token, conteudo, "gravar", expected_sha256=sha)
    assert de_novo.status_code == 302
    assert _contar(app, Execution, ids["usuario"]) == 1
    assert _contar(app, MyprofitColeta, ids["usuario"]) == 1
    assert _contar(app, MyprofitPosition, ids["usuario"]) == 4


def test_coleta_pela_tela_recusa_o_que_nao_e_pacote_e_o_que_passa_do_limite(caso):
    app, ids = caso
    client, token = client_for(app, ids["sessao"])

    assert _enviar_coleta(client, token, b'{"formato": "outro"}', "ensaio").status_code == 422
    planilha = client.post(
        "/executions/collection",
        data={"csrf_token": token, "action": "ensaio", "pacote": (BytesIO(b"x"), "coleta.xlsx")},
        content_type="multipart/form-data",
    )
    assert planilha.status_code == 422
    assert _contar(app, MyprofitColeta, ids["usuario"]) == 0


def test_coleta_e_aplicacao_exigem_login_e_csrf(caso):
    app, ids = caso
    client, _ = client_for(app, ids["sessao"])
    conteudo = _coleta(ids)

    sem_csrf = client.post(
        "/executions/collection", data={"action": "gravar", "pacote": (BytesIO(conteudo), "coleta.json")},
        content_type="multipart/form-data",
    )
    assert sem_csrf.status_code in (400, 403)
    assert client.post("/executions/apply", data={"action": "gravar"}).status_code in (400, 403)
    anonimo = app.test_client()
    for caminho in ("/executions/collection", "/executions/apply"):
        assert anonimo.get(caminho).status_code in (302, 401, 403)
        assert anonimo.post(caminho, data={"action": "gravar"}).status_code in (302, 400, 401, 403)
    assert _contar(app, Execution, ids["usuario"]) == 0


# --- Aplicar negócios -----------------------------------------------------------------

def _posicoes(app, ids):
    with app.app_context():
        return [
            (posicao.quantity, posicao.opened_on)
            for posicao in db.session.scalars(select(Position).where(Position.owner_id == ids["usuario"]))
        ]


def _aplicadas(app, ids) -> int:
    with app.app_context():
        return db.session.scalar(
            select(func.count()).select_from(Execution).where(
                Execution.owner_id == ids["usuario"], Execution.applied_at.is_not(None)
            )
        )


def test_aplicar_pela_tela_ensaia_sem_mexer_e_grava_o_que_o_ensaio_mostrou(caso):
    app, ids = caso
    client, token = client_for(app, ids["sessao"])
    conteudo = _coleta(ids)
    sha = _campo(_enviar_coleta(client, token, conteudo, "ensaio").get_data(as_text=True), "expected_sha256")
    assert _enviar_coleta(client, token, conteudo, "gravar", expected_sha256=sha).status_code == 302

    tela = client.get("/executions/apply").get_data(as_text=True)
    assert "<strong>1</strong> execução(ões) pendente(s)" in tela

    ensaio = client.post("/executions/apply", data={"csrf_token": token, "action": "ensaio"})
    html = ensaio.get_data(as_text=True)
    assert ensaio.status_code == 200
    assert "Ensaio: nada foi gravado" in html and "Aplicada" in html and ids["simbolo"] in html
    assert "Gravar 1 negócio(s)" in html
    assert _posicoes(app, ids) == [] and _aplicadas(app, ids) == 0
    esperado = _campo(html, "expected")

    outro = client.post("/executions/apply", data={"csrf_token": token, "action": "gravar", "expected": "0" * 64})
    assert outro.status_code == 409
    assert _posicoes(app, ids) == [] and _aplicadas(app, ids) == 0

    gravado = client.post("/executions/apply", data={"csrf_token": token, "action": "gravar", "expected": esperado})
    assert gravado.status_code == 200
    assert "Gravado" in gravado.get_data(as_text=True)
    assert _posicoes(app, ids) == [(D("10"), date(2026, 10, 15))]
    assert _aplicadas(app, ids) == 1

    depois = client.post("/executions/apply", data={"csrf_token": token, "action": "ensaio"}).get_data(as_text=True)
    assert "Nenhuma execução pendente" in depois


def test_aplicar_pela_tela_deixa_pendente_o_que_pede_decisao(caso):
    app, ids = caso
    client, token = client_for(app, ids["sessao"])
    negocios = _tsv_dos_negocios([linha(date(2026, 10, 15), "NC-1", "SEMCADASTRO3", 10, "-101.50")])
    conteudo = _pacote(ids["sufixo"], negocios=negocios)
    sha = _campo(_enviar_coleta(client, token, conteudo, "ensaio").get_data(as_text=True), "expected_sha256")
    assert _enviar_coleta(client, token, conteudo, "gravar", expected_sha256=sha).status_code == 302

    html = client.post("/executions/apply", data={"csrf_token": token, "action": "ensaio"}).get_data(as_text=True)

    assert "Pendente" in html and "não está cadastrado no CRV" in html
    assert "Gravar " not in html.split("</form>")[0]  # sem nada aplicável, não há o que gravar
    assert _posicoes(app, ids) == [] and _aplicadas(app, ids) == 0


def test_data_invalida_na_aplicacao_e_recusada(caso):
    app, ids = caso
    client, _ = client_for(app, ids["sessao"])

    assert client.get("/executions/apply?until=ontem").status_code == 400
