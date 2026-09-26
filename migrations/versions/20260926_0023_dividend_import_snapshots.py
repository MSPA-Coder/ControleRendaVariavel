"""Snapshots financeiros necessários aos proventos importados.

Revision ID: 20260926_0023
Revises: 20260926_0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260926_0023"
down_revision = "20260926_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("invested_total", "market_total", "average_price", "quoted_price", "amount_per_share"):
        op.add_column("dividends", sa.Column(name, sa.Numeric(precision=24, scale=8), nullable=True))
        op.create_check_constraint(
            op.f(f"ck_dividends_{name}_non_negative"),
            "dividends",
            f"{name} IS NULL OR {name} >= 0",
        )


def downgrade() -> None:
    for name in ("amount_per_share", "quoted_price", "average_price", "market_total", "invested_total"):
        op.drop_constraint(op.f(f"ck_dividends_{name}_non_negative"), "dividends", type_="check")
        op.drop_column("dividends", name)
