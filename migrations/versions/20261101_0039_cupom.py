"""Tipo de renda `cupom`: o cupom de título de renda fixa que o CB registra.

O enum nativo `income_kind` guarda o NOME do membro (`DIVIDENDO`, `JCP`,
`ALUGUEL`; ver 20260814_0005), então o valor novo é `CUPOM`.
"""

from alembic import op

revision = "20261101_0039"
down_revision = "20261028_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE income_kind ADD VALUE IF NOT EXISTS 'CUPOM'")


def downgrade() -> None:
    # O PostgreSQL não remove valor de enum; um cupom gravado impediria a volta.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM dividends WHERE kind = 'CUPOM') THEN "
        "RAISE EXCEPTION 'Há proventos do tipo cupom: apague-os antes de voltar a migração.'; END IF; END $$"
    )
