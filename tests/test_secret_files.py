"""Segredos por arquivo falham fechados e não precisam entrar no ambiente."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.secret_files import environment_value, project_secret_value


def test_environment_value_prefere_arquivo_ao_valor_do_ambiente(tmp_path: Path) -> None:
    path = tmp_path / "secret_key"
    path.write_text("do-arquivo\n", encoding="utf-8")

    assert environment_value(
        "SECRET_KEY", {"SECRET_KEY": "do-ambiente", "SECRET_KEY_FILE": str(path)}
    ) == "do-arquivo"


@pytest.mark.parametrize("content", ["", "\n"])
def test_environment_value_recusa_arquivo_vazio(tmp_path: Path, content: str) -> None:
    path = tmp_path / "secret_key"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(RuntimeError, match="SECRET_KEY_FILE"):
        environment_value("SECRET_KEY", {"SECRET_KEY_FILE": str(path)})


def test_environment_value_recusa_arquivo_ausente(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD_FILE"):
        environment_value("POSTGRES_PASSWORD", {"POSTGRES_PASSWORD_FILE": str(tmp_path / "none")})


def test_project_secret_value_encontra_o_caminho_padrao(tmp_path: Path) -> None:
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    (secrets_dir / "postgres_password").write_text("senha-de-teste\n", encoding="utf-8")

    assert project_secret_value(tmp_path, "POSTGRES_PASSWORD", {}) == "senha-de-teste"


def test_url_do_postgres_escapa_a_senha_sem_registrar_valor() -> None:
    """O contrato mudou de lugar, não de exigência.

    A montagem da URL saiu de `app.secret_files` para
    `sharedauth.config.montar_url_postgres`, compartilhada com o MegaSena e o
    ConfortoTermico. O teste continua aqui porque o que ele guarda é uma
    exigência *deste* app: a senha do Postgres deste projeto pode conter
    barra e espaço, e sem escape a URL apontaria para outro lugar.
    """
    from sharedauth.config import montar_url_postgres

    assert montar_url_postgres(
        usuario="investimentos",
        senha="senha/com espaço",
        host="db",
        banco="investimentos",
        porta="5432",
    ) == "postgresql+psycopg://investimentos:senha%2Fcom%20espa%C3%A7o@db:5432/investimentos"
