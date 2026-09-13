"""Os destinos são fixos; não há configuração legada para alterná-los."""

from __future__ import annotations

import pytest
from conftest import CONFIG_DE_TESTE

from app import CHAVE_TEMA_NA_SESSAO, create_app, login_manager
from app.core.themes import DEFAULT_THEME
from app.models import ROLE_ADMIN, User

CONFIG_BASE: dict[str, object] = {
    **CONFIG_DE_TESTE,
    "WTF_CSRF_ENABLED": False,
}


def _app(*, remoto: bool):
    return create_app({**CONFIG_BASE, "REMOTE_COLLECTOR_ENABLED": remoto})


def _login_as_admin(client, monkeypatch) -> None:
    usuario = User(
        id=1,
        username="chefe",
        role=ROLE_ADMIN,
        is_active_user=True,
        must_change_password=False,
    )
    monkeypatch.setattr(login_manager, "_user_callback", lambda _user_id: usuario)
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
        session[CHAVE_TEMA_NA_SESSAO] = DEFAULT_THEME


@pytest.mark.parametrize("remoto", [True, False])
def test_rota_legada_nao_existe(remoto) -> None:
    client = _app(remoto=remoto).test_client()
    assert client.post("/settings/collector/destination").status_code == 404


def test_local_recusa_checkbox_que_nao_poderia_iniciar_processo(monkeypatch) -> None:
    client = _app(remoto=False).test_client()
    _login_as_admin(client, monkeypatch)
    assert client.post("/partials/rtd-service", data={"enabled": "on"}).status_code == 409
