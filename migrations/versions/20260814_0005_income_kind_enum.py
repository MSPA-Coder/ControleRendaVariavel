"""dividends.kind vira enum nativo (income_kind).

`Dividend.kind` era `Mapped[IncomeKind]` sobre uma coluna `String(12)` com
CHECK — sem o adaptador `Enum()` do SQLAlchemy, o ORM nunca desserializava
para uma instância de `IncomeKind`, e sim para a string crua vinda do banco.
`==` contra string continuava funcionando (`IncomeKind` é `StrEnum`), mas
`is` e `.name` quebrariam silenciosamente em qualquer código futuro que
assumisse ter de fato uma instância do enum.

O restante do modelo já usa `Enum(EnumClass, name=...)` para os StrEnum
(``collector_mode``, ``position_side``, ``option_type``, ...), e por padrão
o SQLAlchemy grava o **nome** do membro, não o valor -- é por isso que
`position_side` guarda ``'BUY'``/``'SELL'`` embora `Side.BUY.value` seja
``'C'``. `dividends.kind` até aqui gravava o *valor* (``'dividendo'``, via o
CHECK de 20260814_0003), então a conversão precisa maiúsculizar os dados
existentes para ficar consistente com o resto do modelo.

O CHECK `ck_dividends_ck_dividends_kind_valid` (nome duplicado pela convenção
de nomes do Alembic sobre o nome já passado em 20260814_0003) fica redundante:
o tipo enum nativo do Postgres já rejeita qualquer valor fora de
DIVIDENDO/JCP/ALUGUEL.

Revision ID: 20260814_0005_income_kind_enum
Revises: 20260814_0004_ledger_archive
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260814_0005_income_kind_enum"
down_revision = "20260814_0004_ledger_archive"
branch_labels = None
depends_on = None

INCOME_KIND_LABELS = ("DIVIDENDO", "JCP", "ALUGUEL")


def upgrade() -> None:
    op.execute("ALTER TABLE dividends DROP CONSTRAINT ck_dividends_ck_dividends_kind_valid")
    op.execute("ALTER TABLE dividends ALTER COLUMN kind DROP DEFAULT")

    income_kind = postgresql.ENUM(*INCOME_KIND_LABELS, name="income_kind")
    income_kind.create(op.get_bind(), checkfirst=True)

    op.execute(
        "ALTER TABLE dividends "
        "ALTER COLUMN kind TYPE income_kind "
        "USING (UPPER(kind)::income_kind)"
    )
    op.execute("ALTER TABLE dividends ALTER COLUMN kind SET DEFAULT 'DIVIDENDO'::income_kind")


def downgrade() -> None:
    op.execute("ALTER TABLE dividends ALTER COLUMN kind DROP DEFAULT")
    op.execute(
        "ALTER TABLE dividends "
        "ALTER COLUMN kind TYPE VARCHAR(12) "
        "USING (LOWER(kind::text))"
    )
    op.execute("ALTER TABLE dividends ALTER COLUMN kind SET DEFAULT 'dividendo'")

    op.create_check_constraint(
        "ck_dividends_kind_valid",
        "dividends",
        sa.text("kind IN ('dividendo', 'jcp', 'aluguel')"),
    )

    income_kind = postgresql.ENUM(*INCOME_KIND_LABELS, name="income_kind")
    income_kind.drop(op.get_bind(), checkfirst=True)
