"""Remove o índice de `owner_id` do outbox do patrimônio, coberto pelo composto.

`ix_patrimonio_v4_outbox_owner_id` é prefixo de
`ix_patrimonio_v4_outbox_owner_cursor (owner_id, cursor)`: toda consulta por
dono, inclusive `owner_id IS NULL`, usa o composto. O índice sobrava em cada
UPSERT de cotação (a tabela mais escrita do banco). Idempotente.
"""

from __future__ import annotations

from alembic import op

revision = "20261111_0045"
down_revision = "20261111_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_patrimonio_v4_outbox_owner_id")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patrimonio_v4_outbox_owner_id "
        "ON public.patrimonio_v4_outbox USING btree (owner_id)"
    )
