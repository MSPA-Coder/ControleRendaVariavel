"""Coleta do myProfit importada (tabela nova, aditiva).

Uma linha por pacote ``myprofit-coleta/1`` importado: ``sha256`` reconhece o
pacote repetido, ``ate`` diz até quando ele vai e ``summary`` guarda o resumo da
conferência de proventos, que o status de fechamento lê.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261021_0036"
down_revision = "20261020_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "myprofit_coletas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("ate", sa.Date(), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("length(sha256) = 64", name="sha256_length"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_myprofit_coletas_owner_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_myprofit_coletas"),
        sa.UniqueConstraint("owner_id", "sha256", name="uq_myprofit_coletas_owner_sha256"),
    )
    op.create_index("ix_myprofit_coletas_owner_ate", "myprofit_coletas", ["owner_id", "ate"])


def downgrade() -> None:
    op.drop_index("ix_myprofit_coletas_owner_ate", table_name="myprofit_coletas")
    op.drop_table("myprofit_coletas")
