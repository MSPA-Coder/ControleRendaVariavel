"""Add is_benchmark to tickers.

Substitui a lista fixa de símbolos (``BOVA11``, ``USDBRL=X``) usada até
aqui pelos comparadores de evolução em Cotações e Performance por um
cadastro explícito: qualquer ticker pode ser marcado como referência em
Tabelas > Tickers, sem precisar de uma posição para ter sua cotação
histórica atualizada (ver ``app.routes.helpers.quote_update_targets``).

Revision ID: 20260804_14
Revises: 20260802_13
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op

revision = "20260804_14"
down_revision = "20260802_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickers",
        sa.Column(
            "is_benchmark", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    # Migra o cadastro manual existente (Fase D / BENCHMARK_SYMBOLS): os
    # tickers já usados como referência continuam funcionando sem exigir
    # que o usuário os marque de novo manualmente.
    op.execute(
        "UPDATE tickers SET is_benchmark = true WHERE symbol IN ('BOVA11', 'USDBRL=X')"
    )


def downgrade() -> None:
    op.drop_column("tickers", "is_benchmark")
