"""Remove o modo de resultado (`result_mode`, B bruto ou L líquido).

O modo L multiplicava o resultado por 0,9996, herança de uma versão com bugs
da planilha de origem: o custo incidia sobre o resultado, e não sobre o
volume, e numa perda deixava o número melhor. O sistema passa a mostrar só o
resultado bruto; custos reais e IR são apurados fora dele.

Nada é recalculado. `transactions.result` e `*_movements.result` guardam o
resultado realizado como foi calculado no encerramento, e continuam assim:
são fato histórico. Perde-se só a marca de qual modo gerou cada um.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260925_0021"
down_revision = "20260924_0020"
branch_labels = None
depends_on = None

TABELAS = ("positions", "option_positions", "transactions")


def upgrade() -> None:
    # Sem `drop_constraint` pelo nome, como na 20260923_0019: bancos adotados
    # de schema antigo têm as checagens com o prefixo duplicado, e o DROP
    # COLUMN já leva junto as constraints que só tratam da coluna.
    for tabela in TABELAS:
        op.drop_column(tabela, "result_mode")


def downgrade() -> None:
    for tabela in TABELAS:
        op.add_column(
            tabela,
            sa.Column("result_mode", sa.String(1), nullable=False, server_default="L"),
        )
        # `op.f` impede a convenção de nomes de prefixar "ck_<tabela>_" de novo.
        op.create_check_constraint(
            op.f(f"ck_{tabela}_result_mode_valid"), tabela, "result_mode IN ('L', 'B')"
        )
