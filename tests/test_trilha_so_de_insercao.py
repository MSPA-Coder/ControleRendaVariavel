"""A trilha de auditoria aceita inserção do papel da aplicação, e nada além.

O papel `investimentos_app` tem DML em todas as tabelas (o `db-provision`
reconcede a cada subida), então a proteção é um gatilho, e não um REVOKE. A
exceção é a anulação de `user_id` que a FK `ON DELETE SET NULL` faz ao apagar
um usuário. O administrador (superusuário) continua podendo expurgar.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.models import User
from tests.conftest import _url_do_banco_de_teste

pytestmark = pytest.mark.banco


def _usuario(sessao) -> int:
    usuario = User(username=f"trilha-{uuid4().hex[:8]}", password_hash="!")
    sessao.add(usuario)
    sessao.flush()
    return usuario.id


def _registro(sessao, usuario_id: int | None) -> int:
    return sessao.execute(
        text(
            "INSERT INTO audit_log (user_id, entity, action, details) "
            "VALUES (:u, 'Teste', 'criar', '{\"k\": 1}') RETURNING id"
        ),
        {"u": usuario_id},
    ).scalar_one()


def _recusado(sessao, sql: str, **params) -> None:
    with pytest.raises(DBAPIError, match="apenas insercao") as erro, sessao.begin_nested():
        sessao.execute(text(sql), params)
    assert getattr(erro.value.orig, "sqlstate", None) == "42501"


def test_papel_da_aplicacao_insere_mas_nao_altera_nem_apaga_a_trilha(sessao):
    usuario = _usuario(sessao)
    registro = _registro(sessao, usuario)

    _recusado(sessao, "UPDATE audit_log SET action = 'apagar' WHERE id = :i", i=registro)
    _recusado(sessao, "UPDATE audit_log SET details = '{}' WHERE id = :i", i=registro)
    _recusado(sessao, "DELETE FROM audit_log WHERE id = :i", i=registro)

    assert sessao.execute(
        text("SELECT action, user_id FROM audit_log WHERE id = :i"), {"i": registro}
    ).one() == ("criar", usuario)


def test_papel_da_aplicacao_nao_reatribui_nem_anula_o_autor_a_mao(sessao):
    """Só a ação referencial anula `user_id`; trocar o autor por outro é alteração."""
    autor, outro = _usuario(sessao), _usuario(sessao)
    registro = _registro(sessao, autor)

    _recusado(sessao, "UPDATE audit_log SET user_id = :o WHERE id = :i", o=outro, i=registro)
    _recusado(
        sessao,
        "UPDATE audit_log SET user_id = NULL, details = '{}' WHERE id = :i",
        i=registro,
    )


def test_apagar_usuario_anula_o_autor_e_preserva_o_resto_da_trilha(sessao):
    """A exclusão de usuário (FK `SET NULL`) não pode quebrar por causa do gatilho."""
    usuario = _usuario(sessao)
    registro = _registro(sessao, usuario)

    sessao.execute(text("DELETE FROM users WHERE id = :u"), {"u": usuario})

    assert sessao.execute(
        text("SELECT user_id, entity, action, details FROM audit_log WHERE id = :i"),
        {"i": registro},
    ).one() == (None, "Teste", "criar", {"k": 1})


def test_administrador_continua_podendo_corrigir_e_expurgar(app_com_banco):
    engine = create_engine(_url_do_banco_de_teste(administrativo=True))
    try:
        with engine.connect() as conexao:
            registro = conexao.execute(text(
                "INSERT INTO audit_log (entity, action) VALUES ('Teste', 'criar') RETURNING id"
            )).scalar_one()
            conexao.execute(text("UPDATE audit_log SET action = 'x' WHERE id = :i"), {"i": registro})
            conexao.execute(text("DELETE FROM audit_log WHERE id = :i"), {"i": registro})
            conexao.rollback()
    finally:
        engine.dispose()
