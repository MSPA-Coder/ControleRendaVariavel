"""Marca de troca de senha pendente nas contas.

Contas existentes nascem com a marca desligada. Ligá-la para todo mundo
obrigaria quem já usa o sistema a trocar a senha ao mesmo tempo, sem aviso, e
não há motivo: a senha dessas pessoas não é conhecida por terceiros. A marca
passa a ser ligada apenas pela criação de conta e pela redefinição feita por
um administrador.

Coluna nova, com padrão no servidor: a imagem anterior a ignora, o que mantém
a migração compatível com o rollback de código e imagem do `deploy.sh` (que
não reverte schema).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260830_0012"
down_revision = "20260829_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
