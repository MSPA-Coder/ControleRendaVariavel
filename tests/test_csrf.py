"""Escritas exigem token CSRF.

O `TESTING = True` do Flask-WTF desligaria a verificacao; a fixture daqui a
religa de proposito, porque desligar exatamente o controle que se quer medir
tornaria o teste decorativo.
"""

from __future__ import annotations

import pytest
from conftest import CONFIG_DE_TESTE

from app import create_app


@pytest.fixture
def client_com_csrf():
    application = create_app({**CONFIG_DE_TESTE, "WTF_CSRF_ENABLED": True})
    return application.test_client()


def test_post_sem_token_e_rejeitado(client_com_csrf):
    resposta = client_com_csrf.post("/login", data={"username": "x", "password": "y"})
    assert resposta.status_code == 400


def test_post_com_token_invalido_e_rejeitado(client_com_csrf):
    resposta = client_com_csrf.post(
        "/login", data={"csrf_token": "token-inventado", "username": "x", "password": "y"}
    )
    assert resposta.status_code == 400


def test_formulario_de_login_traz_o_campo_do_token(client_com_csrf):
    corpo = client_com_csrf.get("/login").get_data(as_text=True)
    assert 'name="csrf_token"' in corpo


def test_token_valido_e_aceito(app):
    """O caminho positivo, que faltava.

    Exercita o validador direto em vez de fazer o POST completo: um POST
    aceito entraria na view de login, que consulta o banco, e a suite minima
    nao tem banco por desenho. O que se mede aqui e a decisao do CSRF, que e
    exatamente o que estava faltando cobrir.
    """
    from flask_wtf.csrf import generate_csrf, validate_csrf

    app.config["WTF_CSRF_ENABLED"] = True
    with app.test_request_context():
        validate_csrf(generate_csrf())  # nao levantar e o resultado esperado


def test_referrer_policy_nao_anula_o_origin(client):
    """A causa de uma falha real no projeto irmao em Django.

    `Referrer-Policy: no-referrer` faz o navegador serializar `Origin` como
    `null` tambem em POST de mesma origem (Fetch spec), e uma verificacao de
    CSRF que consulte `Origin` recusa a requisicao com o token correto. Aqui a
    verificacao nao consulta `Origin`, mas o cabecalho e compartilhado entre os
    quatro projetos: reintroduzi-lo aqui voltaria a propaga-lo.
    """
    assert client.get("/login").headers.get("Referrer-Policy") != "no-referrer"
