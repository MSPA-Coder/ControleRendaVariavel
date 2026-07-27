"""Normalize brokers and tickers.

Revision ID: 20260727_04
Revises: 20260727_03
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260727_04"
down_revision = "20260727_03"
branch_labels = None
depends_on = None

market = postgresql.ENUM("B3", "NYSE", "NASDAQ", name="market", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    conflicts = bind.execute(
        sa.text(
            """
            SELECT ticker
            FROM positions
            GROUP BY ticker
            HAVING count(DISTINCT (market, rtd_market_code, currency)) > 1
            """
        )
    ).scalars().all()
    if conflicts:
        raise RuntimeError(
            "Tickers com metadados divergentes impedem a migração: "
            + ", ".join(conflicts)
        )

    op.create_table(
        "brokers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("name", name="uq_brokers_name"),
    )
    op.create_table(
        "tickers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column("market", market, nullable=False),
        sa.Column("rtd_market_code", sa.String(length=1), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.UniqueConstraint("symbol", name="uq_tickers_symbol"),
        sa.CheckConstraint(
            "rtd_market_code IN ('B', 'Y', 'N')",
            name="ck_tickers_rtd_market_code_valid",
        ),
        sa.CheckConstraint(
            "currency IN ('BRL', 'USD')",
            name="ck_tickers_currency_valid",
        ),
    )
    op.execute(sa.text("INSERT INTO brokers (name) SELECT DISTINCT broker FROM positions"))
    op.execute(
        sa.text(
            """
            INSERT INTO tickers (symbol, market, rtd_market_code, currency)
            SELECT DISTINCT ticker, market, rtd_market_code, currency
            FROM positions
            """
        )
    )
    op.add_column("positions", sa.Column("broker_id", sa.Integer(), nullable=True))
    op.add_column("positions", sa.Column("ticker_id", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE positions AS p
            SET broker_id = b.id
            FROM brokers AS b
            WHERE b.name = p.broker
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE positions AS p
            SET ticker_id = t.id
            FROM tickers AS t
            WHERE t.symbol = p.ticker
            """
        )
    )
    op.alter_column("positions", "broker_id", nullable=False)
    op.alter_column("positions", "ticker_id", nullable=False)
    op.create_foreign_key(
        "fk_positions_broker_id_brokers",
        "positions",
        "brokers",
        ["broker_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_positions_ticker_id_tickers",
        "positions",
        "tickers",
        ["ticker_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_positions_broker_id", "positions", ["broker_id"])
    op.create_index("ix_positions_ticker_id", "positions", ["ticker_id"])

    # Preserve the original populated columns as an in-place migration backup.
    # New rows use the normalized foreign keys as their only source of truth.
    for column_name in ("broker", "ticker", "market", "rtd_market_code", "currency"):
        op.alter_column("positions", column_name, nullable=True)


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE positions AS p
            SET broker = b.name
            FROM brokers AS b
            WHERE b.id = p.broker_id AND p.broker IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE positions AS p
            SET ticker = t.symbol,
                market = t.market,
                rtd_market_code = t.rtd_market_code,
                currency = t.currency
            FROM tickers AS t
            WHERE t.id = p.ticker_id
              AND (p.ticker IS NULL OR p.market IS NULL
                   OR p.rtd_market_code IS NULL OR p.currency IS NULL)
            """
        )
    )
    for column_name in ("broker", "ticker", "market", "rtd_market_code", "currency"):
        op.alter_column("positions", column_name, nullable=False)
    op.drop_index("ix_positions_ticker_id", table_name="positions")
    op.drop_index("ix_positions_broker_id", table_name="positions")
    op.drop_constraint("fk_positions_ticker_id_tickers", "positions", type_="foreignkey")
    op.drop_constraint("fk_positions_broker_id_brokers", "positions", type_="foreignkey")
    op.drop_column("positions", "ticker_id")
    op.drop_column("positions", "broker_id")
    op.drop_table("tickers")
    op.drop_table("brokers")
