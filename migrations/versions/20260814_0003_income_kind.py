"""Natureza da renda: dividendo, JCP ou aluguel de acoes.

Ate aqui `dividends` guardava um valor recebido sem dizer de que ele veio. A
distincao nao e fiscal, e de precificacao: o historico de cotacoes importado
do Yahoo embutia dividendo e JCP no preco (`adjclose`), mas nenhuma fonte de
preco embute aluguel de acoes. Com a importacao passando a gravar o `close`
nominal, as tres rendas entram no retorno pelo cadastro -- e para isso
precisam ser distinguiveis.

As linhas existentes viram `dividendo`: no momento desta migracao a
instalacao nao tem nenhuma renda cadastrada, e `dividendo` e o valor que o
formulario ja oferecia implicitamente.

Revision ID: 20260814_0003_income_kind
Revises: 20260814_0002_user_role
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260814_0003_income_kind"
down_revision = "20260814_0002_user_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dividends",
        sa.Column("kind", sa.String(length=12), nullable=False, server_default="dividendo"),
    )
    op.create_check_constraint(
        "ck_dividends_kind_valid",
        "dividends",
        sa.text("kind IN ('dividendo', 'jcp', 'aluguel')"),
    )


def downgrade() -> None:
    op.drop_constraint("ck_dividends_kind_valid", "dividends", type_="check")
    op.drop_column("dividends", "kind")
