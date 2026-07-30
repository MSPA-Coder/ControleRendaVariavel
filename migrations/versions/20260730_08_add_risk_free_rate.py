"""Add risk_free_rate_annual to app_settings (options Greeks, item 4).

Revision ID: 20260730_08
Revises: 20260729_07
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

revision = "20260730_08"
down_revision = "20260729_07"
branch_labels = None
depends_on = None

DEFAULT_RISK_FREE_RATE_ANNUAL = "0.1075"


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "risk_free_rate_annual",
            sa.Numeric(5, 4),
            nullable=False,
            server_default=DEFAULT_RISK_FREE_RATE_ANNUAL,
        ),
    )
    op.create_check_constraint(
        "risk_free_rate_annual_range",
        "app_settings",
        "risk_free_rate_annual BETWEEN 0 AND 1",
    )


def downgrade() -> None:
    op.drop_constraint("risk_free_rate_annual_range", "app_settings", type_="check")
    op.drop_column("app_settings", "risk_free_rate_annual")
