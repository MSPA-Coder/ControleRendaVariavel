"""O que resta de segredo por arquivo neste projeto: o caminho do host.

A resolução `NOME_FILE` antes de `NOME` migrou para `sharedauth.secrets`, e é
testada lá (`test_arquivo_tem_precedencia_sobre_a_variavel_direta`,
`test_arquivo_vazio_e_recusado`, `test_arquivo_ausente_e_recusado`). O que
sobra aqui é a busca em `.secrets/` na raiz do projeto, que só este projeto
faz — o agente RTD roda no Windows, fora de contêiner.
"""

from __future__ import annotations

from pathlib import Path

from app.secret_files import project_secret_value


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
