"""Execuções importadas do myProfit, sem alterar posições, transações ou proventos."""

import sqlalchemy as sa
from alembic import op

revision = "20261018_0030"
down_revision = "20261017_0029"
branch_labels = None
depends_on = None

_NAO_FINITO = "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)"


def _finito(coluna: str) -> str:
    return f"{coluna} NOT IN {_NAO_FINITO}"


def upgrade() -> None:
    op.create_table(
        "execution_imports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(120), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("rows_created", sa.Integer(), nullable=False),
        sa.Column("rows_updated", sa.Integer(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_execution_imports_owner_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_execution_imports"),
        sa.UniqueConstraint("id", "owner_id", name="uq_execution_imports_id_owner"),
        sa.CheckConstraint("period_end >= period_start", name="period_valid"),
        sa.CheckConstraint("row_count >= 0 AND rows_created >= 0 AND rows_updated >= 0", name="counts_non_negative"),
        sa.CheckConstraint("length(sha256) = 64", name="sha256_length"),
    )
    op.create_index("ix_execution_imports_owner_id", "execution_imports", ["owner_id"])

    op.create_table(
        "executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("broker_id", sa.Integer(), nullable=False),
        sa.Column("institution", sa.String(40), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("document", sa.String(80), nullable=False),
        sa.Column("document_type", sa.String(40), nullable=True),
        sa.Column("line_order", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("asset", sa.String(80), nullable=False),
        sa.Column("asset_group", sa.String(20), nullable=False),
        sa.Column("side", sa.String(1), nullable=False),
        sa.Column("cash_operation", sa.String(1), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("price_without_fees", sa.Numeric(24, 8), nullable=False),
        sa.Column("price_with_fees", sa.Numeric(24, 8), nullable=False),
        sa.Column("fees_total", sa.Numeric(24, 8), nullable=False),
        sa.Column("total_without_fees", sa.Numeric(24, 8), nullable=False),
        sa.Column("total_with_fees", sa.Numeric(24, 8), nullable=False),
        sa.Column("day_trade", sa.Boolean(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("in_scope", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_executions_owner_id_users", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["broker_id"], ["brokers.id"], name="fk_executions_broker_id_brokers", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["import_id", "owner_id"],
            ["execution_imports.id", "execution_imports.owner_id"],
            name="fk_executions_import_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_executions"),
        sa.UniqueConstraint("owner_id", "document", "line_order", name="uq_executions_owner_document_line"),
        sa.CheckConstraint("line_order >= 1", name="line_order_positive"),
        sa.CheckConstraint("length(content_hash) = 64", name="content_hash_length"),
        sa.CheckConstraint("side IN ('C', 'V')", name="side_valid"),
        sa.CheckConstraint("cash_operation IN ('C', 'D')", name="cash_operation_valid"),
        sa.CheckConstraint(
            "asset_group IN ('acao', 'opcao', 'etf', 'indice_futuro', 'tesouro_direto', 'acao_eua', 'etf_usa')",
            name="asset_group_valid",
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_valid"),
        sa.CheckConstraint("quantity <> 0", name="quantity_non_zero"),
        sa.CheckConstraint("price_without_fees >= 0 AND price_with_fees >= 0", name="prices_non_negative"),
        sa.CheckConstraint("fees_total >= 0", name="fees_non_negative"),
        sa.CheckConstraint(_finito("quantity"), name="quantity_finite"),
        sa.CheckConstraint(_finito("price_without_fees"), name="price_without_fees_finite"),
        sa.CheckConstraint(_finito("price_with_fees"), name="price_with_fees_finite"),
        sa.CheckConstraint(_finito("fees_total"), name="fees_total_finite"),
        sa.CheckConstraint(_finito("total_without_fees"), name="total_without_fees_finite"),
        sa.CheckConstraint(_finito("total_with_fees"), name="total_with_fees_finite"),
    )
    op.create_index("ix_executions_owner_id", "executions", ["owner_id"])
    op.create_index("ix_executions_owner_broker_date", "executions", ["owner_id", "broker_id", "trade_date"])
    op.create_index("ix_executions_import_id", "executions", ["import_id"])


def downgrade() -> None:
    op.drop_table("executions")
    op.drop_table("execution_imports")
