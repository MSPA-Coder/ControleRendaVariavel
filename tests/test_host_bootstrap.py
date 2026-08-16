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
from app.rtd_service import PowerShellAutomationController


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def test_resolve_docker_cli_aceita_apenas_candidato_padrao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidato = tmp_path / "Docker" / "Docker" / "resources" / "bin" / "docker.exe"
    candidato.parent.mkdir(parents=True)
    candidato.write_text("")
    monkeypatch.setattr(host_bootstrap, "_docker_cli_candidates", lambda: (candidato,))

    resultado = host_bootstrap.resolve_docker_cli()

    assert resultado == candidato.resolve()


def test_resolve_docker_cli_sem_candidato_padrao_levanta_erro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(host_bootstrap, "_docker_cli_candidates", lambda: (tmp_path / "docker.exe",))

    with pytest.raises(RuntimeError):
        host_bootstrap.resolve_docker_cli()


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


def test_compose_up_sobe_a_pilha(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    comandos: list[list[str]] = []
    monkeypatch.setattr(host_bootstrap, "_project_directory", lambda: tmp_path)

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        comandos.append(command)
        return _completed(0)

    host_bootstrap.compose_up(Path("docker"), run=run)

    assert comandos == [
        ["docker", "compose", "--project-directory", str(tmp_path), "up", "-d"]
    ]


def test_compose_up_falha_levanta_erro_com_detalhe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(host_bootstrap, "_project_directory", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="deu ruim"):
        host_bootstrap.compose_up(
            Path("docker"),
            run=lambda *a, **k: _completed(1, stderr="deu ruim"),
        )


def test_automacao_rtd_recusa_script_fora_do_projeto(tmp_path: Path) -> None:
    projeto = tmp_path / "projeto"
    esperado = projeto / "scripts" / "rtd-host.ps1"
    esperado.parent.mkdir(parents=True)
    esperado.write_text("# script sintético", encoding="utf-8")
    externo = tmp_path / "externo.ps1"
    externo.write_text("# script sintético", encoding="utf-8")

    with pytest.raises(RuntimeError, match="scripts/rtd-host.ps1"):
        PowerShellAutomationController(projeto, script=externo)


def test_automacao_rtd_aceita_apenas_script_versionado_do_projeto(tmp_path: Path) -> None:
    projeto = tmp_path / "projeto"
    esperado = projeto / "scripts" / "rtd-host.ps1"
    esperado.parent.mkdir(parents=True)
    esperado.write_text("# script sintético", encoding="utf-8")

    controller = PowerShellAutomationController(projeto, script=esperado)

    assert controller.script == esperado.resolve()
