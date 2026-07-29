from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select

from app import db
from app.models import OptionQuote, Quote

HeartbeatStatus = Literal["online", "stale", "error", "waiting"]


def collector_heartbeat(*, stale_after_seconds: int) -> dict[str, str | None]:
    """Summarize the latest persisted RTD reading without exposing quote values."""

    snapshots = db.session.execute(
        select(Quote.observed_at, Quote.source_status).union_all(
            select(OptionQuote.observed_at, OptionQuote.source_status)
        )
    ).all()
    if not snapshots:
        return {"status": "waiting", "last_read_at": None}

    latest_read_at = max(snapshot.observed_at for snapshot in snapshots)
    statuses = {snapshot.source_status for snapshot in snapshots}
    now = datetime.now(UTC)
    if "error" in statuses:
        status: HeartbeatStatus = "error"
    elif now - latest_read_at > timedelta(seconds=stale_after_seconds):
        status = "stale"
    else:
        status = "online"
    return {"status": status, "last_read_at": latest_read_at.isoformat()}
