"""Ligar o Docker do host, sem um Docker real.

``run``, ``sleep`` e ``monotonic`` são sempre injetados: nenhum destes testes
chama um processo de verdade, o que os mantém rápidos e portáteis para além
do Windows onde o coletor RTD de fato roda.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import host_bootstrap


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def test_resolve_docker_cli_usa_o_path_quando_disponivel() -> None:
    resultado = host_bootstrap.resolve_docker_cli(which=lambda name: "/usr/bin/docker")

    assert resultado == Path("/usr/bin/docker")


def test_resolve_docker_cli_cai_para_candidato_do_docker_desktop(tmp_path: Path) -> None:
    candidato = tmp_path / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe"
    candidato.parent.mkdir(parents=True)
    candidato.write_text("")

    resultado = host_bootstrap.resolve_docker_cli(
        which=lambda name: None, env={"LOCALAPPDATA": str(tmp_path), "ProgramFiles": ""}
    )

    assert resultado == candidato


def test_resolve_docker_cli_sem_nenhum_candidato_levanta_erro(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        host_bootstrap.resolve_docker_cli(
            which=lambda name: None,
            env={"LOCALAPPDATA": str(tmp_path), "ProgramFiles": str(tmp_path)},
        )


def test_wait_for_docker_retorna_na_primeira_resposta_saudavel() -> None:
    chamadas: list[float] = []
    host_bootstrap.wait_for_docker(
        Path("docker"),
        run=lambda *a, **k: _completed(0),
        sleep=chamadas.append,
        monotonic=lambda: 0.0,
    )

    assert chamadas == []


def test_wait_for_docker_tenta_de_novo_ate_responder() -> None:
    respostas = iter([_completed(1), _completed(1), _completed(0)])
    sono: list[float] = []

    host_bootstrap.wait_for_docker(
        Path("docker"),
        run=lambda *a, **k: next(respostas),
        sleep=sono.append,
        monotonic=lambda: 0.0,
        poll_interval_seconds=5,
    )

    assert sono == [5, 5]


def test_wait_for_docker_estoura_o_timeout() -> None:
    relogio = iter([0.0, 0.0, 10.0, 10.0])

    with pytest.raises(RuntimeError):
        host_bootstrap.wait_for_docker(
            Path("docker"),
            run=lambda *a, **k: _completed(1),
            sleep=lambda seconds: None,
            monotonic=lambda: next(relogio),
            timeout_seconds=5,
        )


def test_compose_up_sobe_a_pilha() -> None:
    comandos: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        comandos.append(command)
        return _completed(0)

    host_bootstrap.compose_up(Path("docker"), Path("/projeto"), run=run)

    assert comandos == [
        ["docker", "compose", "--project-directory", "/projeto", "up", "-d"]
    ]


def test_compose_up_falha_levanta_erro_com_detalhe() -> None:
    with pytest.raises(RuntimeError, match="deu ruim"):
        host_bootstrap.compose_up(
            Path("docker"),
            Path("/projeto"),
            run=lambda *a, **k: _completed(1, stderr="deu ruim"),
        )
