"""Destino da coleta: o VPS ou o banco desta máquina.

Um coletor só passa a servir aos dois modos, e a escolha entre eles vira
configuração em vez de qual tarefa do Windows está instalada. O padrão é
``remote`` para que uma instalação existente continue entregando ao VPS sem
que ninguém precise escolher nada.

Só a instância que roda na máquina do ProfitChart consulta esta coluna. No
VPS ela existe (é o mesmo schema) e não é lida.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_0014"
down_revision = "20260901_0013"
branch_labels = None
depends_on = None

DESTINATION = sa.Enum("REMOTE", "LOCAL", name="collector_destination")


def upgrade() -> None:
    DESTINATION.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "app_settings",
        sa.Column(
            "collector_destination",
            DESTINATION,
            nullable=False,
            server_default="REMOTE",
        ),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "collector_destination")
    DESTINATION.drop(op.get_bind(), checkfirst=True)
