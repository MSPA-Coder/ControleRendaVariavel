"""Arquiva cadastros referenciados em vez de prendê-los para sempre.

Tickers, corretoras e carteiras que já participam de posições ou do extrato
histórico mantêm suas chaves estrangeiras. A coluna separa essa preservação da
disponibilidade em novos formulários: sem apagar fatos e sem deixar cadastros
inúteis ocupando as listas de operação.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260901_0013"
down_revision = "20260830_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("brokers", "tickers", "portfolios"):
        op.add_column(
            table_name,
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )


def downgrade() -> None:
    for table_name in ("portfolios", "tickers", "brokers"):
        op.drop_column(table_name, "is_active")
