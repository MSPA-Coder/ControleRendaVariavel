"""Invalidates the v4 source view when a closed activity changes.

Revision ID: 20260929_0026
Revises: 20260929_0025
"""

from __future__ import annotations

from alembic import op

revision = "20260929_0026"
down_revision = "20260929_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_patrimonio_v4_outbox_resource_valid",
        "patrimonio_v4_outbox",
        type_="check",
    )
    op.create_check_constraint(
        "ck_patrimonio_v4_outbox_resource_valid",
        "patrimonio_v4_outbox",
        "resource IN ('holding', 'income', 'price_current', 'price_history', 'position_ledger', 'activity')",
    )
    op.execute(
        "CREATE TRIGGER patrimonio_v4_outbox_transactions "
        "AFTER INSERT OR UPDATE OR DELETE ON transactions "
        "FOR EACH ROW EXECUTE FUNCTION patrimonio_v4_outbox_emit('activity', 'id', 'owner_id')"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER patrimonio_v4_outbox_transactions ON transactions")
    # These are invalidation notices only; remove them before restoring the
    # older resource constraint. No financial records are touched.
    op.execute("DELETE FROM patrimonio_v4_outbox WHERE resource = 'activity'")
    op.drop_constraint(
        "ck_patrimonio_v4_outbox_resource_valid",
        "patrimonio_v4_outbox",
        type_="check",
    )
    op.create_check_constraint(
        "ck_patrimonio_v4_outbox_resource_valid",
        "patrimonio_v4_outbox",
        "resource IN ('holding', 'income', 'price_current', 'price_history', 'position_ledger')",
    )
