"""Estado do agente de cotações Windows no servidor remoto.

O agente mantém a integração COM/ProfitChart no Windows e entrega as leituras
ao VPS por HTTPS. A configuração e o pedido manual pertencem ao servidor para
que a mesma tela funcione tanto localmente quanto pelo domínio público.

Revision ID: 20260818_0006_remote_agent
Revises: 20260814_0005_income_kind_enum
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260818_0006_remote_agent"
down_revision = "20260814_0005_income_kind_enum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("collector_refresh_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "app_settings",
        sa.Column("collector_agent_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "app_settings",
        sa.Column(
            "collector_agent_status",
            sa.String(length=16),
            nullable=False,
            server_default="waiting",
        ),
    )
    op.add_column(
        "app_settings",
        sa.Column("collector_agent_error", sa.String(length=250), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "collector_agent_error")
    op.drop_column("app_settings", "collector_agent_status")
    op.drop_column("app_settings", "collector_agent_seen_at")
    op.drop_column("app_settings", "collector_refresh_requested_at")
