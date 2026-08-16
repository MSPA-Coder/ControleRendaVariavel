"""Ligar o Docker Desktop e subir a pilha, a partir do host Windows.

Substitui ``Resolve-DockerCli`` e ``Wait-ForDocker`` de
``scripts/rtd-host-common.ps1`` — a única lógica que continua justificada em
PowerShell é o registro da tarefa agendada (``scripts/rtd-host.ps1``), que
não tem equivalente em Python. Isto aqui é chamado pelo próprio processo do
controlador RTD (``app.rtd_control_server``) antes de servir, então precisa
ser testável sem um Docker real: toda chamada de processo é injetável.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path

RunFunc = Callable[..., subprocess.CompletedProcess[str]]

_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_POLL_INTERVAL_SECONDS = 5.0


def _docker_cli_candidates() -> tuple[Path, ...]:
    """Devolve somente os locais padrão do Docker Desktop no Windows.

    O controlador RTD é iniciado por tarefa agendada e não deve herdar um
    executável de ``PATH`` ou de variável de ambiente alterada. Os dois locais
    cobrem a instalação por máquina e a instalação por usuário do Docker
    Desktop, respectivamente.
    """
    return (
        Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"),
        Path.home() / "AppData" / "Local" / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe",
    )


def resolve_docker_cli() -> Path:
    """Localiza um executável canônico do Docker Desktop, sem consultar PATH."""
    for candidate in _docker_cli_candidates():
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    raise RuntimeError("Docker CLI não encontrado nos locais padrão do Docker Desktop.")


def _project_directory() -> Path:
    """Raiz imutável do projeto que contém este controlador do host."""
    return Path(__file__).resolve().parent.parent


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


def compose_up(docker_cli: Path, *, run: RunFunc = subprocess.run) -> None:
    """``docker compose up -d`` como rede de segurança.

    Com ``restart: unless-stopped`` nos serviços, os contêineres já devem
    voltar sozinhos quando o Docker Desktop reinicia — esta chamada cobre o
    caso de a pilha nunca ter subido nesta máquina, ou de alguém ter rodado
    ``down`` manualmente.
    """
    result = run(
        [str(docker_cli), "compose", "--project-directory", str(_project_directory()), "up", "-d"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        suffix = f" ({detail})" if detail else ""
        raise RuntimeError(f"Falha ao iniciar a pilha Docker{suffix}.")
