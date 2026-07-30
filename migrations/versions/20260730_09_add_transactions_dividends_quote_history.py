"""Add transactions, dividends and quote_history tables (Fase A).

Revision ID: 20260730_09
Revises: 20260730_08
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260730_09"
down_revision = "20260730_08"
branch_labels = None
depends_on = None

position_side = postgresql.ENUM("BUY", "SELL", name="position_side", create_type=False)
position_kind = postgresql.ENUM("REAL", "HYPOTHETICAL", name="position_kind", create_type=False)


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "broker_id",
            sa.Integer(),
            sa.ForeignKey("brokers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ticker_id",
            sa.Integer(),
            sa.ForeignKey("tickers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("average_cost", sa.Numeric(24, 8), nullable=False),
        sa.Column("exit_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("side", position_side, nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.Column("closed_on", sa.Date(), nullable=False),
        sa.Column("result_mode", sa.String(length=1), nullable=False, server_default="L"),
        sa.Column("result", sa.Numeric(24, 8), nullable=False),
        sa.Column(
            "position_kind",
            position_kind,
            nullable=False,
            server_default="REAL",
        ),
        sa.Column("notes", sa.String(length=500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        sa.CheckConstraint("average_cost >= 0", name="average_cost_non_negative"),
        sa.CheckConstraint("exit_price >= 0", name="exit_price_non_negative"),
        sa.CheckConstraint("closed_on >= opened_on", name="closed_on_not_before_opened_on"),
    )
    op.create_index("ix_transactions_broker_id", "transactions", ["broker_id"])
    op.create_index("ix_transactions_ticker_id", "transactions", ["ticker_id"])
    op.create_index("ix_transactions_closed_on", "transactions", ["closed_on"])

    op.create_table(
        "dividends",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "broker_id",
            sa.Integer(),
            sa.ForeignKey("brokers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ticker_id",
            sa.Integer(),
            sa.ForeignKey("tickers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.String(length=500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("amount > 0", name="amount_positive"),
    )
    op.create_index("ix_dividends_broker_id", "dividends", ["broker_id"])
    op.create_index("ix_dividends_ticker_id", "dividends", ["ticker_id"])
    op.create_index("ix_dividends_payment_date", "dividends", ["payment_date"])

    op.create_table(
        "quote_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ticker_id",
            sa.Integer(),
            sa.ForeignKey("tickers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price", sa.Numeric(24, 8), nullable=False),
        sa.Column("recorded_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("price >= 0", name="price_non_negative"),
        sa.UniqueConstraint("ticker_id", "recorded_date", name="uq_quote_history_ticker_date"),
    )
    op.create_index("ix_quote_history_ticker_id", "quote_history", ["ticker_id"])
    op.create_index("ix_quote_history_recorded_date", "quote_history", ["recorded_date"])


def downgrade() -> None:
    op.drop_table("quote_history")
    op.drop_table("dividends")
    op.drop_table("transactions")
