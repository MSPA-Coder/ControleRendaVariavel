"""Posição do myProfit e reconhecimentos da conciliação mensal (tabelas novas, aditivas)."""

import sqlalchemy as sa
from alembic import op

revision = "20261018_0033"
down_revision = "20261018_0032"
branch_labels = None
depends_on = None

_NAO_FINITO = "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)"


def _finito(coluna: str) -> str:
    return f"{coluna} NOT IN {_NAO_FINITO}"


def upgrade() -> None:
    op.create_table(
        "myprofit_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("line_order", sa.Integer(), nullable=False),
        sa.Column("asset", sa.String(80), nullable=False),
        sa.Column("group_label", sa.String(40), nullable=False),
        sa.Column("in_scope", sa.Boolean(), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("average_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("total_invested", sa.Numeric(24, 8), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_myprofit_positions_owner_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_myprofit_positions"),
        sa.UniqueConstraint("owner_id", "as_of", "line_order", name="uq_myprofit_positions_owner_date_line"),
        sa.CheckConstraint("line_order >= 1", name="line_order_positive"),
        sa.CheckConstraint("quantity >= 0 AND average_price >= 0", name="amounts_non_negative"),
        sa.CheckConstraint("length(sha256) = 64", name="sha256_length"),
        sa.CheckConstraint(_finito("quantity"), name="quantity_finite"),
        sa.CheckConstraint(_finito("average_price"), name="average_price_finite"),
        sa.CheckConstraint(_finito("total_invested"), name="total_invested_finite"),
    )
    op.create_index("ix_myprofit_positions_owner_id", "myprofit_positions", ["owner_id"])
    op.create_index("ix_myprofit_positions_owner_as_of", "myprofit_positions", ["owner_id", "as_of"])

    op.create_table(
        "reconciliation_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("asset", sa.String(80), nullable=False),
        sa.Column("note", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_reconciliation_notes_owner_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_notes"),
        sa.UniqueConstraint("owner_id", "kind", "asset", name="uq_reconciliation_notes_owner_kind_asset"),
        sa.CheckConstraint("kind IN ('posicao', 'venda')", name="kind_valid"),
    )
    op.create_index("ix_reconciliation_notes_owner_id", "reconciliation_notes", ["owner_id"])


def downgrade() -> None:
    op.drop_table("reconciliation_notes")
    op.drop_table("myprofit_positions")
