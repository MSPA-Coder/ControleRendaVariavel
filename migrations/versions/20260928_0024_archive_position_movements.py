"""Arquivo completo dos movimentos de posições de ações encerradas.

Revision ID: 20260928_0024
Revises: 20260926_0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260928_0024"
down_revision = "20260926_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_movement_archive",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("broker_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("source_position_id", sa.Integer(), nullable=False),
        sa.Column("source_movement_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("quantity_delta", sa.Numeric(24, 8), nullable=False),
        sa.Column("price", sa.Numeric(24, 8), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("result", sa.Numeric(24, 8), nullable=True),
        # É intencionalmente um valor sem FK: remover uma transação depois não
        # pode apagar a cópia arquivada do movimento.
        sa.Column("source_transaction_id", sa.Integer(), nullable=True),
        sa.Column("resulting_quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("resulting_average_cost", sa.Numeric(24, 8), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "archived_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["broker_id"], ["brokers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["portfolio_id", "owner_id"], ["portfolios.id", "portfolios.owner_id"],
            name="fk_position_movement_archive_portfolio_owner", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "owner_id", "source_position_id", "source_movement_id",
            name="uq_position_movement_archive_source",
        ),
        sa.CheckConstraint("side IN ('C', 'V')", name="ck_position_movement_archive_side_valid"),
        sa.CheckConstraint(
            "kind IN ('open', 'increase', 'decrease', 'adjustment', 'close')",
            name="ck_position_movement_archive_kind_valid",
        ),
        sa.CheckConstraint(
            "quantity_delta NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="ck_position_movement_archive_quantity_delta_finite",
        ),
        sa.CheckConstraint(
            "price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="ck_position_movement_archive_price_finite",
        ),
        sa.CheckConstraint(
            "resulting_quantity NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="ck_position_movement_archive_resulting_quantity_finite",
        ),
        sa.CheckConstraint(
            "resulting_average_cost NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="ck_position_movement_archive_resulting_average_cost_finite",
        ),
        sa.CheckConstraint(
            "result IS NULL OR result NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="ck_position_movement_archive_result_finite",
        ),
        sa.CheckConstraint(
            "(kind IN ('decrease', 'close')) = (result IS NOT NULL)",
            name="ck_position_movement_archive_result_only_on_reduction_or_close",
        ),
        sa.CheckConstraint(
            "(kind IN ('open', 'increase') AND quantity_delta > 0) OR "
            "(kind IN ('decrease', 'close') AND quantity_delta < 0) OR "
            "kind = 'adjustment'",
            name="ck_position_movement_archive_quantity_sign",
        ),
        sa.CheckConstraint(
            "source_transaction_id IS NULL OR kind IN ('decrease', 'close')",
            name="ck_position_movement_archive_transaction_only_on_reduction_or_close",
        ),
        sa.CheckConstraint(
            "kind != 'close' OR "
            "(source_transaction_id IS NOT NULL AND source_movement_id IS NULL "
            "AND resulting_quantity = 0)",
            name="ck_position_movement_archive_close_event_matches_final_transaction",
        ),
        sa.CheckConstraint("price >= 0", name="ck_position_movement_archive_price_non_negative"),
        sa.CheckConstraint(
            "resulting_quantity >= 0",
            name="ck_position_movement_archive_resulting_quantity_non_negative",
        ),
        sa.CheckConstraint(
            "resulting_average_cost >= 0",
            name="ck_position_movement_archive_average_cost_non_negative",
        ),
    )
    op.create_index("ix_position_movement_archive_owner_id", "position_movement_archive", ["owner_id"])
    op.create_index("ix_position_movement_archive_ticker_id", "position_movement_archive", ["ticker_id"])
    op.create_index("ix_position_movement_archive_portfolio_id", "position_movement_archive", ["portfolio_id"])
    op.create_index("ix_position_movement_archive_broker_id", "position_movement_archive", ["broker_id"])
    op.create_index("ix_position_movement_archive_occurred_on", "position_movement_archive", ["occurred_on"])


def downgrade() -> None:
    op.drop_table("position_movement_archive")
