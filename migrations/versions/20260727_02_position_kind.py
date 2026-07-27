"""Classify positions as real or hypothetical.

Revision ID: 20260727_02
Revises: 20260727_01
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260727_02"
down_revision = "20260727_01"
branch_labels = None
depends_on = None

position_kind = postgresql.ENUM(
    "REAL", "HYPOTHETICAL", name="position_kind", create_type=False
)


def upgrade() -> None:
    position_kind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "positions",
        sa.Column(
            "position_kind",
            position_kind,
            nullable=False,
            server_default="REAL",
        ),
    )
    op.execute(
        sa.text(
            "UPDATE positions SET position_kind = 'HYPOTHETICAL' "
            "WHERE lower(broker) = 'av'"
        )
    )


def downgrade() -> None:
    op.drop_column("positions", "position_kind")
    position_kind.drop(op.get_bind(), checkfirst=True)
