"""Tema visual persistido nas configurações.

Revision ID: 20260822_0009_theme
Revises: 20260819_0008_schedule
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260822_0009_theme"
down_revision = "20260819_0008_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("theme", sa.String(length=24), nullable=False, server_default="light"),
    )
    op.create_check_constraint(
        "theme_valid",
        "app_settings",
        "theme IN ('light', 'dark', 'solarized_light', 'solarized_dark', 'dracula', 'nord', "
        "'monokai', 'gray', 'soft_light', 'soft_dark', 'corporate_blue', 'emerald')",
    )


def downgrade() -> None:
    op.drop_constraint("theme_valid", "app_settings", type_="check")
    op.drop_column("app_settings", "theme")
