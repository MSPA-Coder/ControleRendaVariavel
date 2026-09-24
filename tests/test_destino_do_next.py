"""As duas rotas que recebem `next` recusam destino fora do app.

`auth.login` e `privacy.toggle_values_privacy` devolvem o navegador para um
endereço que veio dele. Cada uma validar por conta própria é como as versões
divergem; as duas usarem `url_proximo_seguro` é o contrato. O que se mede aqui
é o efeito -- para onde a resposta manda --, não se a função é citada no
código: a checagem em si tem suíte própria em `sharedauth.access`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import delete

from app import db
from app.models import User
from tests.test_financial_isolation_http import TokenParser
from tests.test_troca_de_senha import _login_as, _usuario

EXTERNOS = ("//externo.test", "https://externo.test/", "/%5cexterno.test")


@pytest.mark.parametrize("destino", EXTERNOS)
def test_alternar_privacidade_nao_redireciona_para_fora(app, client, monkeypatch, destino):
    # O CSRF tem suíte própria (`test_csrf.py`); aqui ele só atrapalharia.
    app.config["WTF_CSRF_ENABLED"] = False
    _login_as(client, _usuario(), monkeypatch)

    resposta = client.post("/privacy/toggle-values", data={"next": destino})

    assert resposta.status_code == 302
    assert "externo.test" not in resposta.headers["Location"]


def test_alternar_privacidade_volta_para_o_destino_local(app, client, monkeypatch):
    # O CSRF tem suíte própria (`test_csrf.py`); aqui ele só atrapalharia.
    app.config["WTF_CSRF_ENABLED"] = False
    _login_as(client, _usuario(), monkeypatch)

    resposta = client.post("/privacy/toggle-values", data={"next": "/portfolio?tab=summary"})

    assert resposta.headers["Location"] == "/portfolio?tab=summary"


@pytest.fixture
def conta_real(app_com_banco):
    senha = "Synthetic-next-only-2026!"
    with app_com_banco.app_context():
        usuario = User(
            username=f"next-{uuid4().hex[:8]}", role="operador", is_active_user=True,
            must_change_password=False,
        )
        usuario.set_password(senha)
        db.session.add(usuario)
        db.session.commit()
        dados = (usuario.id, usuario.username, senha)
    try:
        yield app_com_banco, dados
    finally:
        with app_com_banco.app_context():
            db.session.execute(delete(User).where(User.id == dados[0]))
            db.session.commit()


@pytest.mark.banco
@pytest.mark.parametrize(("destino", "esperado"), [
    ("//externo.test", "/"),
    ("/%5cexterno.test", "/"),
    ("/portfolio?tab=summary", "/portfolio?tab=summary"),
])
def test_login_so_devolve_para_destino_local(conta_real, destino, esperado):
    app, (_id, username, senha) = conta_real
    cliente = app.test_client()
    cliente.environ_base["REMOTE_ADDR"] = f"2001:db8::{uuid4().hex[:4]}"

    formulario = TokenParser()
    formulario.feed(cliente.get("/login").get_data(as_text=True))

    resposta = cliente.post(
        f"/login?next={destino}",
        data={"username": username, "password": senha, "csrf_token": formulario.token},
    )

    assert resposta.status_code == 302
    assert "externo.test" not in resposta.headers["Location"]
    assert resposta.headers["Location"] == esperado
