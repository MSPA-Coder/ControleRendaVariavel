"""Papel do usuario: admin ou operador.

Ate aqui todo usuario autenticado podia tudo. O papel separa quem opera a
carteira de quem altera as configuracoes que mudam os numeros exibidos a todos.

Os usuarios existentes viram `admin`: no momento desta migracao a instalacao
tem um unico usuario, que e o mantenedor, e rebaixa-lo a `operador` o
trancaria fora das proprias configuracoes.

Revision ID: 20260814_0002_user_role
Revises: 20260813_0001_baseline
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260814_0002_user_role"
down_revision = "20260813_0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=20), nullable=False, server_default="operador"),
    )
    op.create_check_constraint(
        "ck_users_role_valid",
        "users",
        sa.text("role IN ('admin', 'operador')"),
    )
    op.execute(sa.text("UPDATE users SET role = 'admin'"))


def downgrade() -> None:
    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.drop_column("users", "role")
