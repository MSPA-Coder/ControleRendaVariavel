"""Retorno visual dos erros esperados na administração de usuários."""

from __future__ import annotations

import json
import re

from app import CHAVE_TEMA_NA_SESSAO, CHAVE_USUARIO_TEMA_NA_SESSAO, login_manager
from app.accounts.users import UserManagementError
from app.core.themes import DEFAULT_THEME
from app.models import ROLE_ADMIN, User


def _login_as_admin(client, monkeypatch) -> None:
    user = User(
        id=1,
        username="admin-teste",
        role=ROLE_ADMIN,
        is_active_user=True,
        must_change_password=False,
    )
    monkeypatch.setattr(login_manager, "_user_callback", lambda _user_id: user)
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
        # O tema já está em sessão depois de um acesso real. Sem essa marca o
        # context processor consulta preferências, o que esta suíte sem banco
        # deliberadamente recusa.
        session[CHAVE_TEMA_NA_SESSAO] = DEFAULT_THEME
        session[CHAVE_USUARIO_TEMA_NA_SESSAO] = user.id


def test_criacao_invalida_por_htmx_devolve_aviso_trocavel(app, client, monkeypatch):
    """O administrador precisa enxergar por que a criação foi recusada."""
    from app.routes import users

    _login_as_admin(client, monkeypatch)
    monkeypatch.setattr(users, "list_users", lambda: [])

    def reject(*_args: str) -> None:
        raise UserManagementError("A confirmação da senha não confere.")

    monkeypatch.setattr(users, "create_user", reject)
    app.config["WTF_CSRF_ENABLED"] = False

    response = client.post(
        "/users",
        data={
            "username": "novo",
            "role": "operador",
            "password": "senha-comprida",
            "password_confirmation": "outra-senha",
        },
        headers={"HX-Request": "true"},
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 422
    assert response.headers["X-App-Request-Error"] == "1"
    assert 'id="users-results"' in body
    avisos = re.search(r"data-sa-avisos='([^']+)'", body)
    assert avisos is not None
    assert json.loads(avisos.group(1))[0]["mensagem"] == "A confirmação da senha não confere."


def test_formulario_de_criacao_anuncia_o_mesmo_minimo_do_servidor(app, client, monkeypatch):
    from app.routes import users

    _login_as_admin(client, monkeypatch)
    monkeypatch.setattr(users, "list_users", lambda: [])

    response = client.get("/users")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert body.count('minlength="10"') == 2
    assert "Use ao menos 10 caracteres" in body
