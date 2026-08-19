"""Agenda de funcionamento do coletor remoto.

Revision ID: 20260819_0008_schedule
Revises: 20260819_0007_agent_check
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260819_0008_schedule"
down_revision = "20260819_0007_agent_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "collector_schedule_weekdays",
            sa.String(length=13),
            nullable=False,
            server_default="0,1,2,3,4",
        ),
    )
    op.add_column(
        "app_settings",
        sa.Column(
            "collector_schedule_start_time",
            sa.Time(),
            nullable=False,
            server_default="09:45:00",
        ),
    )
    op.add_column(
        "app_settings",
        sa.Column(
            "collector_schedule_end_time",
            sa.Time(),
            nullable=False,
            server_default="18:10:00",
        ),
    )
    op.create_check_constraint(
        "collector_schedule_time_range",
        "app_settings",
        "collector_schedule_start_time < collector_schedule_end_time",
    )


def downgrade() -> None:
    op.drop_constraint("collector_schedule_time_range", "app_settings", type_="check")
    op.drop_column("app_settings", "collector_schedule_end_time")
    op.drop_column("app_settings", "collector_schedule_start_time")
    op.drop_column("app_settings", "collector_schedule_weekdays")
