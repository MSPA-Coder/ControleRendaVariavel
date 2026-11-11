"""A sessão do app no PostgreSQL tem limites de tempo (SQL-L1).

Sem eles, um comando preso ou uma espera de trava continuava no servidor depois
de o Gunicorn matar a requisição, e segurava as outras. Os limites ficam nas
opções de conexão do engine do app (não no papel do banco). O padrão é 55 s de
comando, 10 s de trava e 5 min parado dentro de transação; as variáveis
`DB_*_TIMEOUT_MS` ajustam, e 0 desliga.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app import create_app
from tests.conftest import _url_do_banco_de_teste

pytestmark = pytest.mark.banco

VARIAVEIS = ("DB_STATEMENT_TIMEOUT_MS", "DB_LOCK_TIMEOUT_MS", "DB_IDLE_IN_TRANSACTION_TIMEOUT_MS")


def _app(monkeypatch, **limites):
    for variavel in VARIAVEIS:
        monkeypatch.delenv(variavel, raising=False)
    for variavel, valor in limites.items():
        monkeypatch.setenv(variavel, valor)
    return create_app({"SQLALCHEMY_DATABASE_URI": _url_do_banco_de_teste(), "TESTING": True})


def test_sessao_do_app_nasce_com_os_limites_padrao(monkeypatch):
    app = _app(monkeypatch)

    with app.app_context():
        from app import db

        with db.engine.connect() as conexao:
            valores = [
                conexao.execute(text(f"SHOW {nome}")).scalar_one()
                for nome in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")
            ]
        db.engine.dispose()
    assert valores == ["55s", "10s", "5min"]


def test_comando_que_passa_do_limite_e_cancelado_pelo_servidor(monkeypatch):
    app = _app(monkeypatch, DB_STATEMENT_TIMEOUT_MS="300")

    with app.app_context():
        from app import db

        with db.engine.connect() as conexao:
            inicio = time.monotonic()
            with pytest.raises(DBAPIError, match="statement timeout"):
                conexao.execute(text("SELECT pg_sleep(5)"))
            assert time.monotonic() - inicio < 3
        db.engine.dispose()


def test_espera_de_trava_que_passa_do_limite_falha_em_vez_de_pendurar(monkeypatch):
    app = _app(monkeypatch, DB_LOCK_TIMEOUT_MS="300")
    dono = create_engine(_url_do_banco_de_teste(administrativo=True))
    try:
        with dono.connect() as segurando:
            segurando.execute(text("SELECT pg_advisory_xact_lock(918273)"))
            with app.app_context():
                from app import db

                with db.engine.connect() as conexao:
                    # Rede de proteção: sem o limite de trava, a espera acabaria
                    # aqui, e o teste reprovaria em vez de pendurar.
                    conexao.execute(text("SET statement_timeout = 4000"))
                    with pytest.raises(DBAPIError, match="lock timeout"):
                        conexao.execute(text("SELECT pg_advisory_xact_lock(918273)"))
                db.engine.dispose()
            segurando.rollback()
    finally:
        dono.dispose()


def test_transacao_parada_alem_do_limite_e_encerrada_pelo_servidor(monkeypatch):
    app = _app(monkeypatch, DB_IDLE_IN_TRANSACTION_TIMEOUT_MS="300")

    with app.app_context():
        from app import db

        with db.engine.connect() as conexao:
            conexao.execute(text("SELECT 1"))
            time.sleep(1.0)
            with pytest.raises(DBAPIError, match="idle-in-transaction"):
                conexao.execute(text("SELECT 1"))
        db.engine.dispose()


def test_zero_desliga_o_limite_de_comando(monkeypatch):
    """É o que o `migrate` usa: uma migração pode demorar de verdade."""
    app = _app(monkeypatch, DB_STATEMENT_TIMEOUT_MS="0")

    with app.app_context():
        from app import db

        with db.engine.connect() as conexao:
            assert conexao.execute(text("SHOW statement_timeout")).scalar_one() == "0"
        db.engine.dispose()


def test_valor_invalido_recusa_subir_em_vez_de_ignorar(monkeypatch):
    for variavel in VARIAVEIS:
        monkeypatch.delenv(variavel, raising=False)
    monkeypatch.setenv("DB_LOCK_TIMEOUT_MS", "dez segundos")

    with pytest.raises(RuntimeError, match="DB_LOCK_TIMEOUT_MS"):
        create_app({"SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://x:x@localhost/x", "TESTING": True})
