"""Segredo concedido por arquivo no host, fora do contrato do Compose.

O caso do Compose — ``NOME_FILE`` antes de ``NOME``, recusa de ausente e vazio
— mora em :mod:`sharedauth.secrets` e é compartilhado com os outros três
aplicativos. O que resta aqui é o que **só este projeto** tem: o agente RTD
roda no Windows, fora de contêiner, e lê os valores de ``.secrets/`` na raiz do
projeto.

Um consumidor único não justifica mover para a biblioteca — mesmo critério que
manteve a autorização por titular fora dela.

O conteúdo nunca é registrado nem incluído nas exceções.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from sharedauth.secrets import ler_arquivo_de_segredo


def read_secret_file(name: str, path: str | Path) -> str:
    """Lê um segredo sem aceitar arquivo ausente ou vazio."""
    return ler_arquivo_de_segredo(f"{name}_FILE", path)


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
