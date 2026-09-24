"""Remove o "delta da cotação" (`positions.quote_multiplier`).

Era um parâmetro da planilha de origem que não tem papel neste sistema: a
cotação publicada já é o preço do ativo. Todas as posições têm 1.

A revisão ABORTA se alguma posição tiver outro valor. Apagar a coluna nesse
caso mudaria o valor a mercado e o resultado daquela posição sem aviso; quem
tiver um caso assim decide antes, na tela, e roda de novo.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260923_0019"
down_revision = "20260913_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    diferentes = bind.execute(
        sa.text("SELECT count(*) FROM positions WHERE quote_multiplier <> 1")
    ).scalar_one()
    if diferentes:
        raise RuntimeError(
            f"{diferentes} posição(ões) com delta da cotação diferente de 1: "
            "remover a coluna mudaria o valor delas. Ajuste-as antes."
        )
    # Sem `drop_constraint` pelo nome: bancos adotados de schema antigo têm as
    # checagens com o prefixo duplicado (`ck_positions_ck_positions_...`), e o
    # nome esperado não existe neles. O PostgreSQL já remove, com a coluna, as
    # constraints que só tratam dela -- qualquer que seja o nome.
    op.drop_column("positions", "quote_multiplier")


def downgrade() -> None:
    op.add_column(
        "positions",
        sa.Column("quote_multiplier", sa.Numeric(18, 8), nullable=False, server_default="1"),
    )
    # `op.f` impede a convenção de nomes de prefixar "ck_positions_" de novo.
    op.create_check_constraint(
        op.f("ck_positions_quote_multiplier_positive"), "positions", "quote_multiplier > 0"
    )
    op.create_check_constraint(
        op.f("ck_positions_quote_multiplier_finite"),
        "positions",
        "quote_multiplier NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
    )
