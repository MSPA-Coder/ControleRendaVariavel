from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from flask import Flask
from flask_migrate import downgrade as alembic_downgrade
from flask_migrate import upgrade as alembic_upgrade

from app import db
from app.models import Market, QuoteHistory, Ticker

pytestmark = [pytest.mark.critical, pytest.mark.migration_persistence]

_MIGRATIONS_DIR = str(Path(__file__).resolve().parents[2] / "migrations")


def test_financial_integrity_migration_rejects_legacy_non_finite_values(app: Flask) -> None:
    with app.app_context():
        alembic_downgrade(directory=_MIGRATIONS_DIR, revision="20260731_10")
        ticker = Ticker(
            symbol="PETR4",
            trading_name="Petrobras",
            market=Market.B3,
            rtd_market_code="B",
            currency="BRL",
        )
        db.session.add(ticker)
        db.session.commit()
        db.session.add(
            QuoteHistory(
                ticker_id=ticker.id,
                price=Decimal("NaN"),
                recorded_date=date(2026, 8, 1),
                recorded_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
        db.session.commit()

        with pytest.raises(SystemExit):
            alembic_upgrade(directory=_MIGRATIONS_DIR)
