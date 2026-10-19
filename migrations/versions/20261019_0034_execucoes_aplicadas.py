"""Execução do myProfit aplicada às posições (colunas novas, aditivas).

``applied_at`` nulo é execução ainda não refletida nas posições do CRV. Quando
preenchido, ``application`` diz como: ``aplicada`` (o comando abriu, reforçou ou
fechou a posição), ``ja_lancada`` (o CRV já tinha o negócio, digitado à mão) ou
``linha_de_base`` (anterior à adoção, conferida pela conciliação).
"""

import sqlalchemy as sa
from alembic import op

revision = "20261019_0034"
down_revision = "20261018_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("executions", sa.Column("application", sa.String(20), nullable=True))
    op.add_column("executions", sa.Column("application_note", sa.String(300), nullable=True))
    op.create_check_constraint(
        op.f("ck_executions_application_valid"),
        "executions",
        "application IS NULL OR application IN ('aplicada', 'ja_lancada', 'linha_de_base')",
    )
    op.create_check_constraint(
        op.f("ck_executions_application_with_date"),
        "executions",
        "(application IS NULL) = (applied_at IS NULL)",
    )
    op.create_index(
        "ix_executions_pendentes",
        "executions",
        ["owner_id", "trade_date"],
        postgresql_where=sa.text("in_scope AND applied_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_executions_pendentes", table_name="executions")
    op.drop_constraint(op.f("ck_executions_application_with_date"), "executions", type_="check")
    op.drop_constraint(op.f("ck_executions_application_valid"), "executions", type_="check")
    op.drop_column("executions", "application_note")
    op.drop_column("executions", "application")
    op.drop_column("executions", "applied_at")
