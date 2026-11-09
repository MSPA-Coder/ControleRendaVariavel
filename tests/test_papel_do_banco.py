"""A aplicação recusa atender como superusuário do PostgreSQL.

A camada com banco tem os dois papéis de verdade: o administrativo
(`investimentos`, superusuário, que aplica as migrações) e o restrito
(`investimentos_app`, o da aplicação). A recusa e a aceitação são provadas
contra o servidor, e não só contra um dublê.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text

from app.papel_do_banco import (
    VARIAVEL,
    PapelPrivilegiadoError,
    conferir_conexao,
    instalar,
)
from tests.conftest import _url_do_banco_de_teste


def _conectar(url: str) -> None:
    engine = create_engine(url)
    instalar(engine)
    try:
        with engine.connect() as conexao:
            conexao.execute(text("SELECT 1"))
    finally:
        engine.dispose()


@pytest.mark.banco
def test_superusuario_real_e_recusado_quando_exigido(monkeypatch):
    monkeypatch.setenv(VARIAVEL, "1")
    with pytest.raises(PapelPrivilegiadoError):
        _conectar(_url_do_banco_de_teste(administrativo=True))


@pytest.mark.banco
def test_papel_da_aplicacao_passa_quando_exigido(monkeypatch):
    monkeypatch.setenv(VARIAVEL, "1")
    _conectar(_url_do_banco_de_teste())


@pytest.mark.banco
def test_sem_a_variavel_o_papel_administrativo_passa(monkeypatch):
    """O `migrate` usa o superusuário de propósito, sem a variável ligada."""
    monkeypatch.delenv(VARIAVEL, raising=False)
    _conectar(_url_do_banco_de_teste(administrativo=True))


def test_a_fabrica_liga_a_trava_ao_engine(app):
    """Sem o `instalar` em `create_app`, a trava existiria e nunca rodaria."""
    from app import db

    with app.app_context():
        assert event.contains(db.engine, "connect", conferir_conexao)
