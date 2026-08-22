"""A configuração operacional falha fechada antes de atender requisições.

Os testes passam valores sintéticos diretamente para a fábrica e não abrem
conexão com PostgreSQL. O objetivo é impedir que um fallback esconda a ausência
de segredo ou aponte acidentalmente a aplicação para um banco inesperado.
"""

from __future__ import annotations

import pytest

from app import create_app

TEST_DATABASE_URL = "postgresql+psycopg://test:test@localhost:5432/test"


def test_producao_recusa_secret_key_ausente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="Defina SECRET_KEY"):
        create_app({"TESTING": False, "SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL})


def test_producao_recusa_database_url_ausente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="Defina DATABASE_URL"):
        create_app({"TESTING": False, "SECRET_KEY": "test-only-secret"})


def test_producao_resolve_segredos_de_arquivo_para_o_banco_do_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    secret_key_path = tmp_path / "secret_key"
    agent_token_path = tmp_path / "collector_agent_token"
    postgres_password_path = tmp_path / "postgres_password"
    secret_key_path.write_text("chave-de-arquivo", encoding="utf-8")
    postgres_password_path.write_text("senha/de-arquivo", encoding="utf-8")
    agent_token_path.write_text("token-de-arquivo-com-tamanho-suficiente", encoding="utf-8")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY_FILE", str(secret_key_path))
    monkeypatch.setenv("POSTGRES_PASSWORD_FILE", str(postgres_password_path))
    monkeypatch.setenv("COLLECTOR_AGENT_TOKEN_FILE", str(agent_token_path))
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "5432")

    app = create_app({"TESTING": False})

    assert app.config["SECRET_KEY"] == "chave-de-arquivo"
    assert app.config["SQLALCHEMY_DATABASE_URI"] == (
        "postgresql+psycopg://investimentos:senha%2Fde-arquivo@db:5432/investimentos"
    )
    # Era `RTD_CONTROL_TOKEN` ate 2026-08-22, quando o modo de controlador
    # local saiu. A asercao mudou de token, e nao sumiu: o que ela protege e a
    # resolucao de segredo por arquivo `_FILE`, que continua valendo para o
    # token do agente remoto -- e que e o unico segredo de coletor que resta.
    assert app.config["COLLECTOR_AGENT_TOKEN"] == "token-de-arquivo-com-tamanho-suficiente"


def test_engine_usa_pool_pre_ping() -> None:
    """Sem isto, uma conexao morta pelo reinicio do Postgres devolve 500 até
    o pool reciclar sozinho -- ver comentário em `app/__init__.py`."""
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL})

    assert app.config["SQLALCHEMY_ENGINE_OPTIONS"] == {"pool_pre_ping": True}
