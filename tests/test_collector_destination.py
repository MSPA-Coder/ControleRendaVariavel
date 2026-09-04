"""O controle de destino da coleta, na parte que não precisa de banco.

O que estes casos protegem é a recusa no VPS. Esconder o botão no template
não basta: o POST continua alcançável, e trocar aquela linha lá não teria
efeito nenhum -- só deixaria os dois bancos discordando sobre para onde a
coleta está indo.
"""

from __future__ import annotations

import pytest
from conftest import CONFIG_DE_TESTE

from app import CHAVE_TEMA_NA_SESSAO, create_app, login_manager
from app.collector.database import DestinationWatcher
from app.models import ROLE_ADMIN, CollectorDestination, User
from app.themes import DEFAULT_THEME

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


def test_vps_recusa_a_troca_de_destino_mesmo_para_admin(monkeypatch) -> None:
    client = _app(remoto=True).test_client()
    _login_as_admin(client, monkeypatch)

    resposta = client.post("/settings/collector/destination")

    assert resposta.status_code == 403


def test_instancia_local_nao_recusa_a_rota_de_destino(monkeypatch) -> None:
    """Sem 403 na instância local -- o que vem depois já depende do banco."""
    client = _app(remoto=False).test_client()
    _login_as_admin(client, monkeypatch)

    with pytest.raises(Exception) as erro:
        client.post("/settings/collector/destination")

    assert "403" not in str(erro.value)


def test_troca_de_destino_exige_sessao_de_admin() -> None:
    client = _app(remoto=False).test_client()

    resposta = client.post("/settings/collector/destination")

    assert resposta.status_code in (302, 401, 403)


def test_observador_so_reconsulta_o_destino_no_intervalo_de_verificacao() -> None:
    leituras = {"total": 0}
    relogio = {"agora": 0.0}

    def ler() -> CollectorDestination:
        leituras["total"] += 1
        return CollectorDestination.REMOTE

    observador = DestinationWatcher(
        CollectorDestination.REMOTE,
        interval_seconds=30,
        read=ler,
        monotonic=lambda: relogio["agora"],
    )

    assert observador.unchanged() is True
    assert leituras["total"] == 1

    # Vinte e nove segundos depois ainda é a mesma janela: o laço pode ter
    # girado dezenas de vezes, e nenhuma delas custa uma consulta.
    relogio["agora"] = 29.0
    assert observador.unchanged() is True
    assert leituras["total"] == 1

    relogio["agora"] = 30.0
    assert observador.unchanged() is True
    assert leituras["total"] == 2


def test_observador_para_o_laco_quando_o_destino_muda() -> None:
    observador = DestinationWatcher(
        CollectorDestination.REMOTE,
        interval_seconds=0,
        read=lambda: CollectorDestination.LOCAL,
        monotonic=lambda: 0.0,
    )

    assert observador.unchanged() is False
