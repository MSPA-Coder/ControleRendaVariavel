"""Resolve DATABASE_URL/SECRET_KEY para o coletor RTD rodando no host Windows.

Substitui ``Set-RtdCollectorEnvironment`` (regex de PowerShell sobre o
``.env``) por uma implementação testada em Python, evitando duas leituras
independentes do mesmo arquivo. Arquivos explícitos ou locais em ``.secrets``
têm precedência; sem eles, variáveis já presentes no ambiente só são
preenchidas quando ausentes, mesma regra do script que substitui.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, MutableMapping
from pathlib import Path

from app.secret_files import build_postgres_url, project_secret_value

_ASSIGNMENT_RE = re.compile(r"^\s*(\w+)\s*=\s*(.*?)\s*$")
_LOCALHOST_RE = re.compile(r"@localhost(?=[:/])")


def parse_dotenv(path: Path) -> dict[str, str]:
    """Lê pares ``NOME=valor`` de um arquivo ``.env``.

    Aspas simples ou duplas ao redor do valor são removidas. Linhas em
    branco ou sem ``=`` são ignoradas; um comentário (``#...``) só é
    reconhecido quando ocupa a linha inteira, mesma tolerância do parser em
    PowerShell que este módulo substitui.
    """
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _ASSIGNMENT_RE.match(line)
        if not match:
            continue
        name, value = match.group(1), match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def resolve_database_url(
    dotenv: Mapping[str, str],
    existing: str | None,
    *,
    secret_database_url: str | None = None,
    secret_password: str | None = None,
) -> str:
    """Reaproveita ``DATABASE_URL`` do ambiente ou monta a partir do ``.env``.

    Um ``DATABASE_URL`` lido de arquivo ou ``.env`` tem ``localhost`` trocado
    por ``127.0.0.1``: depois de reiniciar o Windows, resolver ``localhost``
    pode tentar ``::1`` primeiro e deixar o coletor bloqueado mesmo com o
    PostgreSQL publicado e saudável. Sem arquivo, um valor já presente no
    ambiente (definido explicitamente por quem chama) não é tocado.
    """
    if secret_database_url:
        return _LOCALHOST_RE.sub("@127.0.0.1", secret_database_url)
    if secret_password:
        return build_postgres_url(
            secret_password,
            host="127.0.0.1",
            port="5302",
            database="investimentos",
            username="investimentos",
        )
    if existing:
        return existing
    raw = dotenv.get("DATABASE_URL")
    if raw:
        return _LOCALHOST_RE.sub("@127.0.0.1", raw)
    password = dotenv.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError(
            "Defina POSTGRES_PASSWORD ou DATABASE_URL no .env para o coletor RTD."
        )
    return build_postgres_url(
        password,
        host="127.0.0.1",
        port="5302",
        database="investimentos",
        username="investimentos",
    )


def resolve_secret_key(
    dotenv: Mapping[str, str], existing: str | None, *, secret_value: str | None = None
) -> str:
    if secret_value:
        return secret_value
    if existing:
        return existing
    value = dotenv.get("SECRET_KEY")
    if not value:
        raise RuntimeError("Defina SECRET_KEY no .env antes de iniciar o coletor RTD.")
    return value


def apply_host_environment(
    project_dir: Path, env: MutableMapping[str, str] | None = None
) -> None:
    """Preenche ``DATABASE_URL``/``SECRET_KEY`` em ``env`` (por padrão, `os.environ`).

    Chamado antes de qualquer subprocesso que precise falar com o Postgres
    publicado pelo Docker — o coletor herda essas variáveis do processo pai,
    então elas precisam estar resolvidas antes do primeiro `Popen`.
    """
    target = os.environ if env is None else env
    dotenv = parse_dotenv(project_dir / ".env")
    secret_database_url = project_secret_value(project_dir, "DATABASE_URL", target)
    secret_password = project_secret_value(project_dir, "POSTGRES_PASSWORD", target)
    secret_key = project_secret_value(project_dir, "SECRET_KEY", target)
    target["DATABASE_URL"] = resolve_database_url(
        dotenv,
        target.get("DATABASE_URL"),
        secret_database_url=secret_database_url,
        secret_password=secret_password,
    )
    target["SECRET_KEY"] = resolve_secret_key(
        dotenv, target.get("SECRET_KEY"), secret_value=secret_key
    )
