"""Add benchmark_ticker_id to app_settings (Fase D: Beta vs. referência).

Revision ID: 20260731_10
Revises: 20260730_09
Create Date: 2026-07-31
"""

import sqlalchemy as sa
from alembic import op

revision = "20260731_10"
down_revision = "20260730_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("benchmark_ticker_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_app_settings_benchmark_ticker_id_tickers",
        "app_settings",
        "tickers",
        ["benchmark_ticker_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_app_settings_benchmark_ticker_id_tickers", "app_settings", type_="foreignkey"
    )
    op.drop_column("app_settings", "benchmark_ticker_id")
