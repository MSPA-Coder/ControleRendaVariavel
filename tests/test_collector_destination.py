"""Destinos fixos: URLs antigas não podem reativar a alternância ou um vigia."""

from __future__ import annotations

import pytest
from conftest import CONFIG_DE_TESTE

from app import CHAVE_TEMA_NA_SESSAO, create_app, login_manager
from app.core.themes import DEFAULT_THEME
from app.models import ROLE_ADMIN, User

# Deriva da configuração do conftest em vez de repeti-la: é dela que vem o
# `creator` que recusa a conexão sem abrir socket. Sem ele, o POST que este
# arquivo faz de propósito ia até o banco, e no Windows a tentativa custava
# 260 s -- sozinha, mais de oito vezes o orçamento da suíte inteira.
CONFIG_BASE: dict[str, object] = {
    **CONFIG_DE_TESTE,
    # O CSRF desta rota é o mesmo de todas as outras e tem suíte própria em
    # test_csrf.py; aqui o que se mede é a decisão por instância.
    "WTF_CSRF_ENABLED": False,
}


def _app(*, remoto: bool):
    return create_app({**CONFIG_BASE, "REMOTE_COLLECTOR_ENABLED": remoto})


def _admin() -> User:
    return User(
        id=1,
        username="chefe",
        role=ROLE_ADMIN,
        is_active_user=True,
        must_change_password=False,
    )


def _login_as_admin(client, monkeypatch) -> None:
    usuario = _admin()
    monkeypatch.setattr(login_manager, "_user_callback", lambda _user_id: usuario)
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
        session[CHAVE_TEMA_NA_SESSAO] = DEFAULT_THEME


@pytest.mark.parametrize("remoto", [True, False])
def test_rota_antiga_recusa_troca_sem_consultar_banco(monkeypatch, remoto) -> None:
    client = _app(remoto=remoto).test_client()
    _login_as_admin(client, monkeypatch)
    assert client.post("/settings/collector/destination").status_code == 410


def test_local_recusa_checkbox_que_nao_poderia_iniciar_processo(monkeypatch) -> None:
    client = _app(remoto=False).test_client()
    _login_as_admin(client, monkeypatch)
    assert client.post("/partials/rtd-service", data={"enabled": "on"}).status_code == 409


def test_troca_de_destino_exige_sessao_de_admin() -> None:
    client = _app(remoto=False).test_client()

    resposta = client.post("/settings/collector/destination")

    assert resposta.status_code in (302, 401, 403)
