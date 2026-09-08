"""Fixtures da suite.

A suite tem DUAS CAMADAS, e a distincao importa ao escrever teste novo.

A CAMADA SEM BANCO e a maioria dos arquivos, e continua sendo desenho e nao
limitacao: cabecalhos, negacao por padrao, CSRF, autorizacao por papel e
integridade do grafo de migracoes sao decididos antes de qualquer consulta, e
mante-la sem banco e o que faz caber no orcamento de 30 segundos. As fixtures
`app` e `client` servem a ela, com um `creator` que recusa conexao.

A CAMADA COM BANCO e o que a fase F1 do LEVANTAMENTO_2026-09.md acrescentou:
os testes marcados com `@pytest.mark.banco`, servidos pelas fixtures
`app_com_banco` e `sessao`. Ela existe porque duas garantias do AGENTS.md eram
impossiveis de verificar sem PostgreSQL:

- que as `CheckConstraint` chegaram ao banco e recusam o que prometem recusar
  -- "quantidade e preco medio nao sao negativos" e os guardas contra NaN e
  Infinity so existem la;
- que a cadeia de migracoes aplica de verdade num banco vazio, e nao apenas
  que o grafo dela e integro.

O banco dessa camada e o servico `db-teste` do Compose: efemero, em tmpfs, e
deliberadamente NAO e o `db` com dados reais.

CONSEQUENCIA PRATICA: o bootstrap do schema em PostgreSQL vazio deixou de ser
verificacao manual. Uma migracao que falha ao executar agora reprova na CI, e
nao mais no `deploy.sh` -- que reverte codigo e imagem, mas nao reverte
migracao.

No venv, sem banco, o laco rapido e `pytest -q -m "not banco"`.
"""

from __future__ import annotations

import os
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Camada com banco (`@pytest.mark.banco`)
# ---------------------------------------------------------------------------


def _url_do_banco_de_teste() -> str:
    """Monta a URL a partir das variaveis `TESTE_POSTGRES_*` do Compose.

    O prefixo `TESTE_` nao e enfeite: varios testes desta suite medem o que a
    aplicacao faz quando `SECRET_KEY` ou `DATABASE_URL` NAO existem, apagando a
    variavel com `monkeypatch.delenv`. Declarar os nomes reais no servico
    `quality` faria o app encontrar por arquivo o que o teste acabou de apagar,
    e seis testes passariam a falhar por causa do ambiente. O motivo tambem
    esta escrito no `compose.yaml`, ao lado das variaveis.
    """
    from urllib.parse import quote

    faltando = [
        nome
        for nome in ("TESTE_POSTGRES_HOST", "TESTE_POSTGRES_DB", "TESTE_POSTGRES_USER")
        if not os.environ.get(nome)
    ]
    if faltando:
        pytest.skip(
            "camada com banco: rode pelo servico `quality` do Compose "
            f"(faltam {', '.join(faltando)})"
        )

    # O caminho e CONSTANTE, e nao uma variavel de ambiente, de proposito.
    # `/run/secrets/<nome>` e onde o Compose monta todo segredo de arquivo, e
    # ler o caminho do ambiente para depois abri-lo e exatamente o padrao que o
    # CodeQL sinaliza como "uncontrolled data used in path expression" -- com
    # razao, ainda que aqui a origem fosse o proprio compose.yaml. Sem o
    # intermediario nao existe sink, e o codigo fica mais curto.
    senha = Path("/run/secrets/postgres_password").read_text(encoding="utf-8").strip()
    return (
        "postgresql+psycopg://"
        f"{quote(os.environ['TESTE_POSTGRES_USER'])}:{quote(senha)}"
        f"@{os.environ['TESTE_POSTGRES_HOST']}:{os.environ.get('TESTE_POSTGRES_PORT', '5432')}"
        f"/{os.environ['TESTE_POSTGRES_DB']}"
    )


@pytest.fixture(scope="session")
def app_com_banco():
    """App real, ligado ao `db-teste`, com a cadeia de migracoes aplicada.

    A configuracao vem por argumento, e nao do ambiente: assim esta camada nao
    altera o ambiente em que o resto da suite roda.

    O `upgrade()` aqui nao e cerimonia: e ele que faz cada execucao da suite
    aplicar TODAS as revisoes Alembic a um banco vazio. Uma revisao com SQL
    invalido, coluna `NOT NULL` acrescentada a tabela com linhas ou dependencia
    de extensao ausente derruba esta fixture, e a CI reprova -- que era
    exatamente o que faltava.
    """
    from flask_migrate import upgrade

    aplicacao = create_app(
        {"SQLALCHEMY_DATABASE_URI": _url_do_banco_de_teste(), "TESTING": True}
    )
    with aplicacao.app_context():
        upgrade()
    return aplicacao


@pytest.fixture
def sessao(app_com_banco):
    """Sessao dentro de uma transacao que sempre e desfeita.

    Os testes desta camada usam `flush()` e nunca `commit()`: o `flush` envia o
    INSERT e faz a `CheckConstraint` disparar, que e o que se quer medir, sem
    deixar linha atras. O `rollback` no fim garante que um teste nao enxergue o
    que o anterior escreveu, mesmo que alguem acrescente um `commit` aqui um
    dia.
    """
    from app import db

    with app_com_banco.app_context():
        try:
            yield db.session
        finally:
            db.session.rollback()
            db.session.remove()
