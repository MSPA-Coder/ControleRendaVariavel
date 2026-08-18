"""Token do controlador RTD: gerado uma vez, reaproveitado nos reinícios.

``main()`` liga Docker de verdade e não é exercitado aqui — só a parte pura
de token, que é o que a tarefa agendada depende para não recriar o
contêiner `web` a cada logon.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rtd_control_server import controller_log_path, read_or_create_token


def test_read_or_create_token_gera_na_primeira_vez(tmp_path: Path) -> None:
    caminho = tmp_path / ".docker-local" / "rtd-control-token"

    token = read_or_create_token(caminho)

    assert len(token) >= 32
    assert caminho.read_text(encoding="utf-8") == token


def test_read_or_create_token_reaproveita_o_existente(tmp_path: Path) -> None:
    caminho = tmp_path / "rtd-control-token"
    caminho.write_text("a" * 40, encoding="utf-8")

    assert read_or_create_token(caminho) == "a" * 40


def test_read_or_create_token_rejeita_token_curto(tmp_path: Path) -> None:
    caminho = tmp_path / "rtd-control-token"
    caminho.write_text("curto", encoding="utf-8")

    with pytest.raises(RuntimeError):
        read_or_create_token(caminho)


def test_controller_log_path_usa_diretorio_local_do_usuario(tmp_path: Path) -> None:
    local_app_data = tmp_path / "AppData" / "Local"

    path = controller_log_path(tmp_path, str(local_app_data))

    assert path == local_app_data / "ControleRendaVariavel" / "rtd-controller.log"
