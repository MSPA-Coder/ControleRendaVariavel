"""O login calcula o hash em todos os caminhos (SEG-2).

Com `user is None or not user.is_active_user or not user.check_password(...)`,
o scrypt (dezenas de ms) só rodava para conta ativa, e o tempo da resposta
entregava quais logins existem e quais estão ativos. A mensagem e o status são
os mesmos; o que se prova aqui é que a verificação de hash roda uma vez em cada
caminho recusado, inclusive o de usuário inexistente. Não se mede milissegundo.
"""

from __future__ import annotations

from unittest import mock

import pytest

from app import db
from app.models import User
from tests.test_financial_isolation_http import client_for
from tests.test_financial_isolation_http import review_case as review_case

pytestmark = pytest.mark.banco

SENHA_CERTA = "Synthetic-review-only-2026!"


def _tentar(app, data, usuario: str, senha: str):
    client, token = client_for(app, data["ids"][0])
    with client.session_transaction() as sessao:
        sessao.clear()
    token = client.get("/login").get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]
    with mock.patch("werkzeug.security.check_password_hash", wraps=__import__(
        "werkzeug.security", fromlist=["check_password_hash"]
    ).check_password_hash) as hash_:
        resposta = client.post(
            "/login", data={"csrf_token": token, "username": usuario, "password": senha}
        )
    return resposta, hash_.call_count


def test_hash_roda_uma_vez_em_todo_caminho_recusado(review_case):
    app, data = review_case
    with app.app_context():
        ativo = db.session.get(User, data["user_ids"][0])
        inativo = db.session.get(User, data["user_ids"][1])
        inativo.is_active_user = False
        db.session.commit()
        nome_ativo, nome_inativo = ativo.username, inativo.username

    casos = {
        "inexistente": ("nao-existe-" + nome_ativo, SENHA_CERTA),
        "inativo": (nome_inativo, SENHA_CERTA),
        "senha_errada": (nome_ativo, "outra-senha"),
    }
    for caminho, (usuario, senha) in casos.items():
        resposta, chamadas = _tentar(app, data, usuario, senha)
        assert resposta.status_code == 401, caminho
        assert "Usuário ou senha inválidos." in resposta.get_data(as_text=True), caminho
        assert chamadas == 1, f"{caminho}: o hash rodou {chamadas} vez(es)"


def test_login_correto_continua_entrando(review_case):
    app, data = review_case
    with app.app_context():
        nome = db.session.get(User, data["user_ids"][0]).username

    resposta, chamadas = _tentar(app, data, nome, SENHA_CERTA)

    assert resposta.status_code == 302
    assert chamadas == 1
