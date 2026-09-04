"""Detecta o ProfitChart interativo no Windows, sem tentar ativar COM.

O coletor espera esta resposta antes de abrir o provedor: pedir uma sessão
COM com o ProfitChart fechado não devolve "fechado", devolve uma instância
nova iniciada pelo próprio pedido -- e aí o Windows fica com um ProfitChart
invisível que ninguém pediu.

Este módulo já foi ``app/rtd_service.py``, que também supervisionava um
processo ``poll-rtd`` iniciado pela aplicação web. Essa supervisão saiu
quando a tarefa do Windows passou a ser a única dona do ciclo de vida do
coletor; sobrou a detecção, que é o que o laço de coleta consulta.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Protocol


class ProfitDetector(Protocol):
    def is_running(self) -> bool: ...


def _hidden_console_kwargs() -> dict[str, object]:
    """Impede que subprocessos de console pisquem na área de trabalho Windows."""
    if sys.platform != "win32":
        return {}
    options: dict[str, object] = {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    startup_info_factory = getattr(subprocess, "STARTUPINFO", None)
    if startup_info_factory is None:
        return options
    startup_info = startup_info_factory()
    startup_info.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    startup_info.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    options["startupinfo"] = startup_info
    return options


class WindowsProfitDetector:
    """Detects the interactive Profit process without attempting COM activation."""

    def is_running(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "$items = Get-CimInstance Win32_Process -Filter "
                    "\"Name = 'ProfitChart.exe'\" -ErrorAction SilentlyContinue | "
                    "Where-Object { $_.CommandLine -notmatch "
                    "'(?:^|\\s)-Embedding(?:\\s|$)' }; @($items).Count",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
                **_hidden_console_kwargs(),
            )
            return int(result.stdout.strip() or "0") > 0
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise RuntimeError("Não foi possível verificar o processo do Profit.") from exc
