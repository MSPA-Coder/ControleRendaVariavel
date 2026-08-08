from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from flask import Flask
from flask_migrate import downgrade as alembic_downgrade
from flask_migrate import upgrade as alembic_upgrade

from app import db

pytestmark = [pytest.mark.critical]

_MIGRATIONS_DIR = str(Path(__file__).resolve().parents[2] / "migrations")


def test_financial_integrity_migration_rejects_non_finite_values(
    app: Flask, rebuild_schema: None
) -> None:
    with app.app_context():
        alembic_downgrade(directory=_MIGRATIONS_DIR, revision="20260731_10")
        # Insere via SQL bruto, não pelo modelo ORM ``Ticker`` atual: nesta
        # revisão antiga, o schema de `tickers` ainda não tem colunas
        # adicionadas depois (ex.: `is_benchmark`), e o modelo ORM sempre
        # reflete o schema mais recente — usá-lo aqui tentaria inserir uma
        # coluna que ainda não existe neste ponto do histórico.
        tickers_table = sa.table(
            "tickers",
            sa.column("symbol"),
            sa.column("trading_name"),
            sa.column("market"),
            sa.column("rtd_market_code"),
            sa.column("currency"),
        )
        ticker_id = db.session.execute(
            tickers_table.insert()
            .values(
                symbol="PETR4",
                trading_name="Petrobras",
                market="B3",
                rtd_market_code="B",
                currency="BRL",
            )
            .returning(sa.column("id"))
        ).scalar_one()
        quote_history_table = sa.table(
            "quote_history",
            sa.column("ticker_id"),
            sa.column("price"),
            sa.column("recorded_date"),
            sa.column("recorded_at"),
        )
        db.session.execute(
            quote_history_table.insert().values(
                ticker_id=ticker_id,
                price=Decimal("NaN"),
                recorded_date=date(2026, 8, 1),
                recorded_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
        db.session.commit()

        with pytest.raises(SystemExit):
            alembic_upgrade(directory=_MIGRATIONS_DIR)
