"""Identidade de um envio de aporte, persistida junto do efeito financeiro.

Uma posição única não distingue retry de um novo aporte. A chave identifica
a intenção; o hash impede reutilizá-la silenciosamente com valores diferentes.
Quem inicia o caso de uso continua responsável por commit/rollback.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app import db
from app.models import PositionSubmission

_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,64}\Z")


def new_submission_token() -> str:
    return secrets.token_urlsafe(24)


def _json_value(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Tipo de entrada não serializável: {type(value).__name__}")


def claim_submission(*, owner_id: int, command: str, token: str, payload: dict[str, object]) -> bool:
    """True no primeiro envio, False no replay com o mesmo conteúdo.

    ON CONFLICT espera a transação concorrente: se ela comitar, lemos a chave
    consumida; se fizer rollback, esta tentativa pode registrá-la. Não há
    confirmação de aporte nem commit escondido nesta função.
    """
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        raise ValueError("Envio sem identificação válida. Reabra o formulário e tente novamente.")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_value)
    fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    key = {"owner_id": owner_id, "command": command, "token": token}
    inserted = db.session.scalar(
        insert(PositionSubmission)
        .values(**key, request_hash=fingerprint)
        .on_conflict_do_nothing(index_elements=["owner_id", "command", "token"])
        .returning(PositionSubmission.token)
    )
    if inserted is not None:
        return True
    previous = db.session.scalar(
        select(PositionSubmission.request_hash).filter_by(**key)
    )
    if previous != fingerprint:
        raise ValueError("Este envio já foi usado com outros valores. Reabra o formulário.")
    return False
