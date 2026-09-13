"""Remove configurações do coletor que não têm mais efeito."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260913_0018"
down_revision = "20260913_0017"
branch_labels = None
depends_on = None

_LEGACY_COLUMNS = ("collector_mode", "collector_destination")
_LEGACY_ENUMS = ("collector_mode", "collector_destination")


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("app_settings")
    }
    for column in _LEGACY_COLUMNS:
        if column in existing_columns:
            op.drop_column("app_settings", column)

    # Não usa CASCADE: se um uso inesperado tiver surgido, a revisão deve
    # abortar integralmente em vez de remover dependências desconhecidas.
    for enum_name in _LEGACY_ENUMS:
        bind.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))


def downgrade() -> None:
    raise RuntimeError("A revisão 20260913_0018 não recria configurações legadas sem efeito.")
