"""Pausa da coleta como configuração, não como processo.

O liga/desliga da tela iniciava e matava um processo ``poll-rtd`` a partir da
aplicação web. Com a tarefa do Windows dona do ciclo de vida do coletor, esse
controle passou a ser incapaz de cumprir o que promete: ligar criaria um
segundo coletor que ficaria preso no lock interprocesso, e desligar
encerraria apenas o filho da própria aplicação, nunca o processo da tarefa.

A pausa passa a ser um fato na tabela, que o coletor lê pela origem ativa --
a linha local quando grava aqui, o payload do VPS quando entrega lá.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_0015"
down_revision = "20260904_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "collector_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "collector_paused")
