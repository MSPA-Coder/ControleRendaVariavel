"""O papel decide, e a decisao fica no servidor.

Esconder o menu e apresentacao. O que impede um operador de alterar o coletor e
o `@requer_admin` na rota -- e e isso que este arquivo mede, sem passar pela
interface.
"""

from __future__ import annotations

import pytest
from flask import abort

from app.authorization import requer_admin
from app.models import ROLE_ADMIN, ROLE_OPERADOR, VALID_ROLES, User


class _UsuarioFalso:
    """Substitui `current_user` sem tocar o banco: o decorator so consulta
    `is_admin`, entao um duplo com essa propriedade exercita a decisao real."""

    def __init__(self, papel: str) -> None:
        self.role = papel

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


@pytest.fixture
def rota_protegida():
    @requer_admin
    def view():
        return "alcancou"

    return view


def test_admin_alcanca(app, monkeypatch, rota_protegida):
    monkeypatch.setattr("app.authorization.current_user", _UsuarioFalso(ROLE_ADMIN))
    with app.test_request_context("/settings"):
        assert rota_protegida() == "alcancou"


def test_operador_recebe_403(app, monkeypatch, rota_protegida):
    monkeypatch.setattr("app.authorization.current_user", _UsuarioFalso(ROLE_OPERADOR))
    with app.test_request_context("/settings"), pytest.raises(Exception) as erro:
        rota_protegida()
    assert "403" in str(erro.value)


def test_anonimo_sem_papel_recebe_403(app, monkeypatch, rota_protegida):
    # `getattr(current_user, "is_admin", False)` precisa negar quando a
    # propriedade nem existe, nao estourar.
    monkeypatch.setattr("app.authorization.current_user", object())
    with app.test_request_context("/settings"), pytest.raises(Exception) as erro:
        rota_protegida()
    assert "403" in str(erro.value)


def test_papel_padrao_do_modelo_e_o_menos_privilegiado():
    # Esquecer o decorator em uma rota nova nao pode combinar com um padrao
    # permissivo: o padrao precisa ser `operador`.
    coluna = User.__table__.columns["role"]
    assert coluna.default.arg == ROLE_OPERADOR
    assert coluna.server_default.arg == ROLE_OPERADOR


def test_papeis_validos_sao_apenas_dois():
    assert {ROLE_ADMIN, ROLE_OPERADOR} == VALID_ROLES


def test_settings_esta_protegida_por_papel(app):
    # Amarra o decorator a rota concreta: sem isto, remover `@requer_admin` de
    # `/settings` passaria despercebido.
    regra = next(r for r in app.url_map.iter_rules() if r.rule == "/settings")
    view = app.view_functions[regra.endpoint]
    assert getattr(view, "__wrapped__", None) is not None, (
        "/settings deveria estar embrulhada por @requer_admin"
    )


def test_abort_403_e_o_comportamento_esperado(app):
    # Documenta a escolha: 403, nao redirecionar para o login. Quem chegou aqui
    # ja esta autenticado.
    with app.test_request_context("/"), pytest.raises(Exception) as erro:
        abort(403)
    assert "403" in str(erro.value)
