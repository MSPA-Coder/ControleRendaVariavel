"""Versão de posições para recusar formulários de edição desatualizados."""

import sqlalchemy as sa
from alembic import op

revision = "20261028_0038"
down_revision = "20261021_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("positions", "option_positions"):
        op.add_column(table, sa.Column("edit_version", sa.BigInteger(), nullable=False, server_default=sa.text("1")))
    op.execute("""
        CREATE FUNCTION incrementar_versao_posicao() RETURNS trigger AS $$
        BEGIN
            NEW.edit_version := OLD.edit_version + 1;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in ("positions", "option_positions"):
        op.execute(f"CREATE TRIGGER versao_edicao BEFORE UPDATE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION incrementar_versao_posicao()")


def downgrade() -> None:
    for table in ("positions", "option_positions"):
        op.execute(f"DROP TRIGGER versao_edicao ON {table}")
    op.execute("DROP FUNCTION incrementar_versao_posicao()")
    for table in ("positions", "option_positions"):
        op.drop_column(table, "edit_version")
