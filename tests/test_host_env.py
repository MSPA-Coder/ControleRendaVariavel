"""Resolução de DATABASE_URL/SECRET_KEY para o coletor RTD no host.

Sem I/O real: ``parse_dotenv`` é a única função que toca disco, e é testada
com um arquivo temporário; as demais recebem o mapeamento já lido.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import host_env


def test_parse_dotenv_ignora_comentario_e_linha_em_branco(tmp_path: Path) -> None:
    arquivo = tmp_path / ".env"
    arquivo.write_text(
        "# comentario\n\nPOSTGRES_PASSWORD=segredo\nSECRET_KEY='chave com espaco'\n",
        encoding="utf-8",
    )

    valores = host_env.parse_dotenv(arquivo)

    assert valores == {
        "POSTGRES_PASSWORD": "segredo",
        "SECRET_KEY": "chave com espaco",
    }


def test_parse_dotenv_arquivo_ausente_devolve_vazio(tmp_path: Path) -> None:
    assert host_env.parse_dotenv(tmp_path / "nao-existe.env") == {}


def test_resolve_database_url_preserva_valor_ja_definido() -> None:
    resultado = host_env.resolve_database_url(
        {"DATABASE_URL": "postgresql://outro"}, existing="postgresql://existente"
    )

    assert resultado == "postgresql://existente"


def test_resolve_database_url_troca_localhost_por_ipv4() -> None:
    resultado = host_env.resolve_database_url(
        {"DATABASE_URL": "postgresql+psycopg://user:pw@localhost:5302/investimentos"},
        existing=None,
    )

    assert resultado == "postgresql+psycopg://user:pw@127.0.0.1:5302/investimentos"


def test_resolve_database_url_monta_a_partir_da_senha() -> None:
    resultado = host_env.resolve_database_url(
        {"POSTGRES_PASSWORD": "s3nha/estranha"}, existing=None
    )

    assert resultado == (
        "postgresql+psycopg://investimentos:s3nha%2Festranha"
        "@127.0.0.1:5302/investimentos"
    )


def test_resolve_database_url_sem_nada_levanta_erro() -> None:
    with pytest.raises(RuntimeError):
        host_env.resolve_database_url({}, existing=None)


def test_resolve_secret_key_preserva_valor_ja_definido() -> None:
    assert host_env.resolve_secret_key({"SECRET_KEY": "do-arquivo"}, "do-ambiente") == (
        "do-ambiente"
    )


def test_resolve_secret_key_le_do_dotenv() -> None:
    assert host_env.resolve_secret_key({"SECRET_KEY": "do-arquivo"}, None) == "do-arquivo"


def test_resolve_secret_key_sem_nada_levanta_erro() -> None:
    with pytest.raises(RuntimeError):
        host_env.resolve_secret_key({}, None)


def test_apply_host_environment_preenche_o_mapeamento_recebido(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "POSTGRES_PASSWORD=segredo\nSECRET_KEY=chave\n", encoding="utf-8"
    )
    env: dict[str, str] = {}

    host_env.apply_host_environment(tmp_path, env)

    assert env["SECRET_KEY"] == "chave"
    assert env["DATABASE_URL"].startswith("postgresql+psycopg://investimentos:segredo@")


def test_apply_host_environment_nao_sobrescreve_variavel_existente(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "POSTGRES_PASSWORD=segredo\nSECRET_KEY=chave\n", encoding="utf-8"
    )
    env = {"DATABASE_URL": "postgresql://ja-configurado"}

    host_env.apply_host_environment(tmp_path, env)

    assert env["DATABASE_URL"] == "postgresql://ja-configurado"


def test_apply_host_environment_usa_arquivos_de_segredo_sem_dotenv(tmp_path: Path) -> None:
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secret_key").write_text("chave-de-teste\n", encoding="utf-8")
    (secrets_dir / "postgres_password").write_text("senha/de-teste\n", encoding="utf-8")
    env: dict[str, str] = {}

    host_env.apply_host_environment(tmp_path, env)

    assert env["SECRET_KEY"] == "chave-de-teste"
    assert env["DATABASE_URL"] == (
        "postgresql+psycopg://investimentos:senha%2Fde-teste@127.0.0.1:5302/investimentos"
    )


def test_apply_host_environment_prefere_database_url_por_arquivo(tmp_path: Path) -> None:
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secret_key").write_text("chave-de-teste", encoding="utf-8")
    (secrets_dir / "database_url").write_text(
        "postgresql+psycopg://user:pw@localhost:5302/outro", encoding="utf-8"
    )
    env: dict[str, str] = {}

    host_env.apply_host_environment(tmp_path, env)

    assert env["DATABASE_URL"] == "postgresql+psycopg://user:pw@127.0.0.1:5302/outro"


def test_apply_host_environment_prefere_senha_de_arquivo_a_url_legada(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+psycopg://legado:legado@localhost:5302/legado\n",
        encoding="utf-8",
    )
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secret_key").write_text("chave-de-teste", encoding="utf-8")
    (secrets_dir / "postgres_password").write_text("senha-nova", encoding="utf-8")
    env: dict[str, str] = {}

    host_env.apply_host_environment(tmp_path, env)

    assert env["DATABASE_URL"] == (
        "postgresql+psycopg://investimentos:senha-nova@127.0.0.1:5302/investimentos"
    )
