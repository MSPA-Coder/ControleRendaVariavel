"""Store collector mode and polling interval.

Revision ID: 20260727_03
Revises: 20260727_02
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260727_03"
down_revision = "20260727_02"
branch_labels = None
depends_on = None

collector_mode = postgresql.ENUM(
    "EXCEL",
    "DIRECT",
    name="collector_mode",
    create_type=False,
)


def upgrade() -> None:
    collector_mode.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "collector_mode",
            collector_mode,
            nullable=False,
            server_default="EXCEL",
        ),
        sa.Column(
            "poll_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id = 1", name="ck_app_settings_singleton"),
        sa.CheckConstraint(
            "poll_interval_seconds BETWEEN 1 AND 3600",
            name="ck_app_settings_poll_interval_seconds_range",
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO app_settings (id, collector_mode, poll_interval_seconds) "
            "VALUES (1, 'EXCEL', 2)"
        )
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    collector_mode.drop(op.get_bind(), checkfirst=True)
