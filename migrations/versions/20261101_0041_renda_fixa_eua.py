"""Negócio do myProfit no grupo "Renda Fixa EUA" (venda de título da Avenue).

O título fica no CB como aplicação (conta de renda fixa EUA); o CRV só registra o
negócio, fora de escopo, como faz com o Tesouro Direto. A constraint do grupo
passa a aceitar `renda_fixa_eua`. Voltar exige apagar antes esses negócios.
"""

from __future__ import annotations

from alembic import op

revision = "20261101_0041"
down_revision = "20261101_0040"
branch_labels = None
depends_on = None

_OLD_GROUPS = ("acao", "opcao", "etf", "indice_futuro", "tesouro_direto", "acao_eua", "etf_usa")
_NEW_GROUPS = _OLD_GROUPS + ("renda_fixa_eua",)


def _recreate(groups: tuple[str, ...]) -> None:
    op.drop_constraint("asset_group_valid", "executions", type_="check")
    values = ", ".join(f"'{group}'" for group in groups)
    op.create_check_constraint("asset_group_valid", "executions", f"asset_group IN ({values})")


def upgrade() -> None:
    _recreate(_NEW_GROUPS)


def downgrade() -> None:
    _recreate(_OLD_GROUPS)
