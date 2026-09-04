from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select

from app import db
from app.models import OptionQuote, Quote

HeartbeatStatus = Literal["online", "stale", "error", "waiting"]


def collector_heartbeat(*, stale_after_seconds: int) -> dict[str, str | None]:
    """Summarize the latest persisted RTD reading without exposing quote values."""

    # Agregado no banco: este resumo é renderizado em toda página, e trazer
    # uma linha por cotação só para calcular um máximo cresce com a carteira.
    snapshots = (
        select(Quote.observed_at, Quote.source_status)
        .union_all(select(OptionQuote.observed_at, OptionQuote.source_status))
        .subquery()
    )
    latest_read_at = db.session.scalar(select(func.max(snapshots.c.observed_at)))
    if latest_read_at is None:
        return {"status": "waiting", "last_read_at": None}

    latest_statuses = set(
        db.session.scalars(
            select(snapshots.c.source_status).where(snapshots.c.observed_at == latest_read_at)
        )
    )
    now = datetime.now(UTC)
    if "error" in latest_statuses:
        status: HeartbeatStatus = "error"
    elif now - latest_read_at > timedelta(seconds=stale_after_seconds):
        status = "stale"
    else:
        status = "online"
    return {"status": status, "last_read_at": latest_read_at.isoformat()}
