"""Create option contracts, positions, quotes, and expirations.

Revision ID: 20260727_05
Revises: 20260727_04
Create Date: 2026-07-27
"""

from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260727_05"
down_revision = "20260727_04"
branch_labels = None
depends_on = None

option_type = postgresql.ENUM("CALL", "PUT", name="option_type", create_type=False)
position_side = postgresql.ENUM("BUY", "SELL", name="position_side", create_type=False)
position_kind = postgresql.ENUM(
    "REAL", "HYPOTHETICAL", name="position_kind", create_type=False
)


def upgrade() -> None:
    option_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "option_expirations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("call_code", sa.String(length=5), nullable=False, unique=True),
        sa.Column("put_code", sa.String(length=5), nullable=False, unique=True),
        sa.Column("exercise_date", sa.Date(), nullable=False, unique=True),
    )
    expiration_table = sa.table(
        "option_expirations",
        sa.column("call_code", sa.String),
        sa.column("put_code", sa.String),
        sa.column("exercise_date", sa.Date),
    )
    op.bulk_insert(
        expiration_table,
        [
            {
                "call_code": f"{year}{call_letter}",
                "put_code": f"{year}{put_letter}",
                "exercise_date": exercise_date,
            }
            for year, call_letter, put_letter, exercise_date in [
                (2024, "F", "R", date(2024, 6, 21)),
                (2024, "G", "S", date(2024, 7, 19)),
                (2024, "H", "T", date(2024, 8, 16)),
                (2024, "I", "U", date(2024, 9, 20)),
                (2024, "J", "V", date(2024, 10, 18)),
                (2024, "K", "W", date(2024, 11, 14)),
                (2024, "L", "X", date(2024, 12, 20)),
                (2025, "A", "M", date(2025, 1, 17)),
                (2025, "B", "N", date(2025, 2, 21)),
                (2025, "C", "O", date(2025, 3, 21)),
                (2025, "D", "P", date(2025, 4, 18)),
                (2025, "E", "Q", date(2025, 5, 16)),
                (2025, "F", "R", date(2025, 6, 20)),
                (2025, "G", "S", date(2025, 7, 18)),
                (2025, "H", "T", date(2025, 8, 22)),
                (2025, "I", "U", date(2025, 9, 19)),
                (2025, "J", "V", date(2025, 10, 17)),
                (2025, "K", "W", date(2025, 11, 21)),
                (2025, "L", "X", date(2025, 12, 19)),
                (2026, "A", "M", date(2026, 1, 16)),
                (2026, "B", "N", date(2026, 2, 20)),
                (2026, "C", "O", date(2026, 3, 20)),
                (2026, "D", "P", date(2026, 4, 17)),
                (2026, "E", "Q", date(2026, 5, 15)),
            ]
        ],
    )
    op.create_table(
        "option_contracts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ticker_id",
            sa.Integer(),
            sa.ForeignKey("tickers.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "underlying_ticker_id",
            sa.Integer(),
            sa.ForeignKey("tickers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "expiration_id",
            sa.Integer(),
            sa.ForeignKey("option_expirations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("option_type", option_type, nullable=False),
        sa.Column("strike", sa.Numeric(24, 8), nullable=False),
        sa.CheckConstraint("strike >= 0", name="ck_option_contracts_strike_non_negative"),
    )
    op.create_table(
        "option_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "broker_id",
            sa.Integer(),
            sa.ForeignKey("brokers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "contract_id",
            sa.Integer(),
            sa.ForeignKey("option_contracts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("average_cost", sa.Numeric(24, 8), nullable=False),
        sa.Column("target_price", sa.Numeric(24, 8)),
        sa.Column("side", position_side, nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.Column("result_mode", sa.String(length=1), nullable=False, server_default="L"),
        sa.Column(
            "position_kind",
            position_kind,
            nullable=False,
            server_default="REAL",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("quantity > 0", name="ck_option_positions_quantity_positive"),
        sa.CheckConstraint(
            "average_cost >= 0",
            name="ck_option_positions_average_cost_non_negative",
        ),
        sa.CheckConstraint(
            "target_price IS NULL OR target_price >= 0",
            name="ck_option_positions_target_non_negative",
        ),
    )
    op.create_index("ix_option_positions_broker_id", "option_positions", ["broker_id"])
    op.create_index(
        "ix_option_positions_contract_id", "option_positions", ["contract_id"]
    )
    op.create_table(
        "option_quotes",
        sa.Column(
            "option_position_id",
            sa.Integer(),
            sa.ForeignKey("option_positions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("previous_close", sa.Numeric(24, 8), nullable=False),
        sa.Column("underlying_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("instrument_status", sa.String(length=16), nullable=False),
        sa.Column("source_status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.String(length=250)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "last_price >= 0", name="ck_option_quotes_last_price_non_negative"
        ),
        sa.CheckConstraint(
            "previous_close >= 0",
            name="ck_option_quotes_previous_close_non_negative",
        ),
        sa.CheckConstraint(
            "underlying_price >= 0",
            name="ck_option_quotes_underlying_price_non_negative",
        ),
    )
    op.create_index(
        "ix_option_quotes_observed_at", "option_quotes", ["observed_at"]
    )


def downgrade() -> None:
    op.drop_table("option_quotes")
    op.drop_table("option_positions")
    op.drop_table("option_contracts")
    op.drop_table("option_expirations")
    option_type.drop(op.get_bind(), checkfirst=True)
