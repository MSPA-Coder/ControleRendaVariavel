"""Add broker acronym and ticker trading name.

Revision ID: 20260728_06
Revises: 20260727_05
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op

revision = "20260728_06"
down_revision = "20260727_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("brokers", sa.Column("acronym", sa.String(length=40), nullable=True))
    op.execute(sa.text("UPDATE brokers SET acronym = name"))
    op.alter_column("brokers", "acronym", nullable=False)
    op.create_unique_constraint("uq_brokers_acronym", "brokers", ["acronym"])

    op.add_column("tickers", sa.Column("trading_name", sa.String(length=80), nullable=True))
    op.execute(sa.text("UPDATE tickers SET trading_name = symbol"))
    op.alter_column("tickers", "trading_name", nullable=False)


def downgrade() -> None:
    op.drop_column("tickers", "trading_name")
    op.drop_constraint("uq_brokers_acronym", "brokers", type_="unique")
    op.drop_column("brokers", "acronym")
