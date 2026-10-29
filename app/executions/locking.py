"""Coordenação das importações e aplicações de execuções por dono."""

from sqlalchemy import text

from app import db


def lock_executions(owner_id: int) -> None:
    """Mantém a exclusão até o commit ou rollback da transação chamadora."""
    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                       {"key": f"myprofit-executions:{owner_id}"})
