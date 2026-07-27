"""Create positions and quote snapshots.

Revision ID: 20260727_01
Revises:
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260727_01"
down_revision = None
branch_labels = None
depends_on = None

market = postgresql.ENUM("B3", "NYSE", "NASDAQ", name="market", create_type=False)
position_side = postgresql.ENUM("BUY", "SELL", name="position_side", create_type=False)


def upgrade() -> None:
    market.create(op.get_bind(), checkfirst=True)
    position_side.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("broker", sa.String(length=40), nullable=False),
        sa.Column("ticker", sa.String(length=24), nullable=False),
        sa.Column("market", market, nullable=False),
        sa.Column("rtd_market_code", sa.String(length=1), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("average_cost", sa.Numeric(24, 8), nullable=False),
        sa.Column("side", position_side, nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("quote_multiplier", sa.Numeric(18, 8), nullable=False),
        sa.Column("target_multiplier", sa.Numeric(18, 8), nullable=False),
        sa.Column("result_mode", sa.String(length=1), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("quantity > 0", name="ck_positions_quantity_positive"),
        sa.CheckConstraint("average_cost >= 0", name="ck_positions_average_cost_non_negative"),
        sa.CheckConstraint("quote_multiplier > 0", name="ck_positions_quote_multiplier_positive"),
        sa.CheckConstraint("target_multiplier > 0", name="ck_positions_target_multiplier_positive"),
    )
    op.create_index("ix_positions_ticker", "positions", ["ticker"])
    op.create_table(
        "quotes",
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("positions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("previous_close", sa.Numeric(24, 8), nullable=False),
        sa.Column("instrument_status", sa.String(length=16), nullable=False),
        sa.Column("source_status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.String(length=250)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("last_price >= 0", name="ck_quotes_last_price_non_negative"),
        sa.CheckConstraint("previous_close >= 0", name="ck_quotes_previous_close_non_negative"),
    )
    op.create_index("ix_quotes_observed_at", "quotes", ["observed_at"])


def downgrade() -> None:
    op.drop_table("quotes")
    op.drop_table("positions")
    position_side.drop(op.get_bind(), checkfirst=True)
    market.drop(op.get_bind(), checkfirst=True)
