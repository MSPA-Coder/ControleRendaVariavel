"""Uma posição por chave (dono, carteira, corretora, ativo/contrato, lado).

O aporte já fundia na posição existente, mas quando ela ainda não existia
não havia linha para o `FOR UPDATE` travar: dois cliques no primeiro aporte
criavam duas posições. E a edição podia mover uma posição para a chave de
outra. O índice único é a garantia do banco; a aplicação serializa o aporte
com lock consultivo e recusa a edição que colide.

A revisão ABORTA se já houver duplicata. Fundir duas posições muda custo
médio, extrato e transação aberta; quem decide é o usuário, na tela.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260924_0020"
down_revision = "20260923_0019"
branch_labels = None
depends_on = None

_CHAVES = (
    ("positions", "uq_positions_chave", "ticker_id"),
    ("option_positions", "uq_option_positions_chave", "contract_id"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for tabela, nome, instrumento in _CHAVES:
        colunas = ["owner_id", "portfolio_id", "broker_id", instrumento, "side"]
        repetidas = bind.execute(
            sa.text(
                f"SELECT count(*) FROM (SELECT 1 FROM {tabela} "  # noqa: S608 - nomes fixos
                f"GROUP BY {', '.join(colunas)} HAVING count(*) > 1) d"
            )
        ).scalar_one()
        if repetidas:
            raise RuntimeError(
                f"{repetidas} chave(s) com mais de uma linha em {tabela}: "
                "junte ou exclua as duplicatas na tela antes de aplicar esta revisão."
            )
        op.create_unique_constraint(nome, tabela, colunas)


def downgrade() -> None:
    for tabela, nome, _instrumento in _CHAVES:
        op.drop_constraint(nome, tabela, type_="unique")
