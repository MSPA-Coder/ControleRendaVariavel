"""Leitura segura de segredos concedidos por arquivo.

Compose monta segredos em ``/run/secrets``; o agente RTD no Windows lê os
valores de ``.secrets`` fora do Git. O conteúdo nunca é registrado nem incluído
nas exceções deste módulo.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def read_secret_file(name: str, path: str | Path) -> str:
    """Lê um segredo sem aceitar arquivo ausente ou vazio."""
    try:
        value = Path(path).read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise RuntimeError(f"Não foi possível ler {name}_FILE.") from exc
    if not value:
        raise RuntimeError(f"{name}_FILE não pode estar vazio.")
    return value


def environment_value(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Resolve ``NOME_FILE`` antes de ``NOME`` sem expor o conteúdo."""
    values = os.environ if environ is None else environ
    file_path = values.get(f"{name}_FILE")
    if file_path is not None:
        if not file_path:
            raise RuntimeError(f"{name}_FILE não pode estar vazio.")
        return read_secret_file(name, file_path)
    return values.get(name)


def project_secret_value(
    project_dir: Path, name: str, environ: Mapping[str, str]
) -> str | None:
    """Resolve caminho explícito ou ``.secrets/<nome em minúsculas>`` do host."""
    file_path = environ.get(f"{name}_FILE")
    if file_path is not None:
        if not file_path:
            raise RuntimeError(f"{name}_FILE não pode estar vazio.")
        return read_secret_file(name, file_path)
    default_path = project_dir / ".secrets" / name.lower()
    if default_path.is_file():
        return read_secret_file(name, default_path)
    return None
