"""Ligar o Docker Desktop e subir a pilha, a partir do host Windows.

Substitui ``Resolve-DockerCli`` e ``Wait-ForDocker`` de
``scripts/rtd-host-common.ps1`` — a única lógica que continua justificada em
PowerShell é o registro da tarefa agendada (``scripts/rtd-host.ps1``), que
não tem equivalente em Python. Isto aqui é chamado pelo próprio processo do
controlador RTD (``app.rtd_control_server``) antes de servir, então precisa
ser testável sem um Docker real: toda chamada de processo é injetável.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path

RunFunc = Callable[..., subprocess.CompletedProcess[str]]

_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_POLL_INTERVAL_SECONDS = 5.0


def resolve_docker_cli(
    *,
    which: Callable[[str], str | None] = shutil.which,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Localiza o executável do Docker CLI, como ``Resolve-DockerCli``."""
    found = which("docker")
    if found:
        return Path(found)
    environ = os.environ if env is None else env
    candidates = [
        Path(environ.get("LOCALAPPDATA", ""))
        / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe",
        Path(environ.get("ProgramFiles", ""))
        / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Docker CLI não encontrado. Instale ou inicie o Docker Desktop.")


def wait_for_docker(
    docker_cli: Path,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    run: RunFunc = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Espera o daemon do Docker responder, com o mesmo timeout de ``Wait-ForDocker``."""
    deadline = monotonic() + timeout_seconds
    while True:
        result = run(
            [str(docker_cli), "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        if monotonic() >= deadline:
            raise RuntimeError(
                f"O Docker Desktop não ficou disponível em {timeout_seconds:.0f} segundos."
            )
        sleep(poll_interval_seconds)


def compose_up(
    docker_cli: Path, project_dir: Path, *, run: RunFunc = subprocess.run
) -> None:
    """``docker compose up -d`` como rede de segurança.

    Com ``restart: unless-stopped`` nos serviços, os contêineres já devem
    voltar sozinhos quando o Docker Desktop reinicia — esta chamada cobre o
    caso de a pilha nunca ter subido nesta máquina, ou de alguém ter rodado
    ``down`` manualmente.
    """
    result = run(
        [str(docker_cli), "compose", "--project-directory", str(project_dir), "up", "-d"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        suffix = f" ({detail})" if detail else ""
        raise RuntimeError(f"Falha ao iniciar a pilha Docker{suffix}.")
