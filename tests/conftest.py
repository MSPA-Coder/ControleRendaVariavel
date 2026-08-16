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

import pytest

from app import create_app

CONFIG_DE_TESTE: dict[str, object] = {
    "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://test:test@localhost:5432/test",
    "TESTING": True,
}


@pytest.fixture
def app():
    return create_app(dict(CONFIG_DE_TESTE))


@pytest.fixture
def client(app):
    return app.test_client()
