"""Configuração do intervalo de verificação do agente Windows.

Revision ID: 20260819_0007_agent_check
Revises: 20260818_0006_remote_agent
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260819_0007_agent_check"
down_revision = "20260818_0006_remote_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "agent_check_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )
    op.create_check_constraint(
        "agent_check_interval_seconds_range",
        "app_settings",
        "agent_check_interval_seconds BETWEEN 5 AND 3600",
    )


def downgrade() -> None:
    op.drop_constraint("agent_check_interval_seconds_range", "app_settings", type_="check")
    op.drop_column("app_settings", "agent_check_interval_seconds")
