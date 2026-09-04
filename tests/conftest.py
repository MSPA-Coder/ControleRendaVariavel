"""Fixtures da suite minima.

A suite nao toca o banco. Isso e desenho, nao limitacao: as coisas que ela
protege -- cabecalhos, negacao por padrao, CSRF, autorizacao por papel e
integridade do grafo de migracoes -- sao decididas antes de qualquer consulta,
e mante-la sem banco e o que faz caber no orcamento de 30 segundos sem
infraestrutura de teste.

O bootstrap do schema em PostgreSQL vazio continua sendo verificacao manual
obrigatoria para mudanca de schema, como a base registra.
"""

from __future__ import annotations

import psycopg
import pytest

from app import create_app


def _banco_inalcancavel() -> object:
    """Recusa toda conexao sem abrir socket.

    "Sem banco" era so a URI apontar para um Postgres que nao existe, e a
    suite dependia de o sistema operacional recusar o TCP na hora. No Linux
    (e no conteiner) recusa e imediata; no Windows nao e. Com psycopg 3.2 e
    Python 3.14, `wait_conn` fica girando no seletor para sempre quando a
    conexao e recusada -- o socket so sinaliza a falha em `exceptfds`, e o
    laco nunca observa isso. Efeito pratico: cada teste que tocava o banco
    travava indefinidamente, e `pytest` no venv do Windows nunca terminava
    (>5 min contra ~18 s no conteiner). `connect_timeout` na URI nao ajuda:
    o prazo e conferido dentro do mesmo laco travado.

    Trocar a espera pela recusa local tira o sistema operacional da conta: a
    falha e a mesma `psycopg.OperationalError` que o SQLAlchemy converteria
    em `sqlalchemy.exc.OperationalError`, so que instantanea e igual em
    qualquer plataforma.
    """
    raise psycopg.OperationalError("suite de testes sem banco: conexao recusada")


CONFIG_DE_TESTE: dict[str, object] = {
    "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://test:test@localhost:5432/test",
    # `creator` substitui o `connect` do driver; a URI acima continua valendo
    # so para escolher o dialeto, e nenhum socket chega a ser aberto.
    "SQLALCHEMY_ENGINE_OPTIONS": {"creator": _banco_inalcancavel},
    "TESTING": True,
}


@pytest.fixture
def app():
    return create_app(dict(CONFIG_DE_TESTE))


@pytest.fixture
def client(app):
    return app.test_client()
