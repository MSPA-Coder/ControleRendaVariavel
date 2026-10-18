"""Identificação transacional dos envios de aporte, sem alterar posições existentes."""

import sqlalchemy as sa
from alembic import op

revision = "20261017_0029"
down_revision = "20261009_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_submissions",
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("command", sa.String(12), primary_key=True),
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("command IN ('stock', 'option')", name="position_submission_command"),
    )


def downgrade() -> None:
    op.drop_table("position_submissions")
