"""Formato regional (Brasil/EUA) de datas e números, escolhido por usuário.

Revision ID: 20261009_0028
Revises: 20261003_0027

Só apresentação: nada do que já está gravado muda. Todo usuário existente fica
em ``br``, o formato que o sistema sempre mostrou. Reverter descarta a escolha.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261009_0028"
down_revision = "20261003_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_preferences",
        sa.Column("regional_format", sa.String(length=2), nullable=False, server_default="br"),
    )
    op.create_check_constraint(
        "regional_format_valid",
        "user_preferences",
        "regional_format IN ('br', 'us')",
    )


def downgrade() -> None:
    op.drop_constraint("regional_format_valid", "user_preferences", type_="check")
    op.drop_column("user_preferences", "regional_format")
