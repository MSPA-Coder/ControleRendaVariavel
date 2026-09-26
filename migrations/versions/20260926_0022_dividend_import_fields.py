"""Campos de origem e chave única dos proventos importados.

Revision ID: 20260926_0022
Revises: 20260925_0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260926_0022"
down_revision = "20260925_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dividends", sa.Column("com_date", sa.Date(), nullable=True))
    op.add_column("dividends", sa.Column("yield_on_cost", sa.Numeric(precision=24, scale=8), nullable=True))
    op.add_column("dividends", sa.Column("dividend_yield", sa.Numeric(precision=24, scale=8), nullable=True))
    op.add_column("dividends", sa.Column("quotas", sa.Numeric(precision=24, scale=8), nullable=True))
    op.create_check_constraint(
        op.f("ck_dividends_yield_on_cost_non_negative"),
        "dividends",
        "yield_on_cost IS NULL OR yield_on_cost >= 0",
    )
    op.create_check_constraint(
        op.f("ck_dividends_dividend_yield_non_negative"),
        "dividends",
        "dividend_yield IS NULL OR dividend_yield >= 0",
    )
    op.create_check_constraint(
        op.f("ck_dividends_quotas_non_negative"),
        "dividends",
        "quotas IS NULL OR quotas >= 0",
    )
    op.create_unique_constraint(
        "import_key",
        "dividends",
        ["owner_id", "ticker_id", "broker_id", "kind", "payment_date", "amount"],
    )


def downgrade() -> None:
    op.drop_constraint("import_key", "dividends", type_="unique")
    op.drop_constraint(op.f("ck_dividends_quotas_non_negative"), "dividends", type_="check")
    op.drop_constraint(op.f("ck_dividends_dividend_yield_non_negative"), "dividends", type_="check")
    op.drop_constraint(op.f("ck_dividends_yield_on_cost_non_negative"), "dividends", type_="check")
    op.drop_column("dividends", "quotas")
    op.drop_column("dividends", "dividend_yield")
    op.drop_column("dividends", "yield_on_cost")
    op.drop_column("dividends", "com_date")
