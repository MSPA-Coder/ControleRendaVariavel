"""Add stale_alert_seconds to app_settings.

Permite ao usuário configurar manualmente, em Configurações, quantos
segundos sem leitura fazem uma cotação ser considerada desatualizada
(sugestão do protótipo do mega menu ainda não coberta pelo app). Quando
não preenchido, mantém o cálculo automático já existente, baseado no
intervalo de coleta configurado (ver ``routes.helpers.quote_stale_after_seconds``).

Revision ID: 20260802_13
Revises: 20260802_12
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op

revision = "20260802_13"
down_revision = "20260802_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("stale_alert_seconds", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "stale_alert_seconds_range",
        "app_settings",
        "stale_alert_seconds IS NULL OR stale_alert_seconds BETWEEN 1 AND 86400",
    )


def downgrade() -> None:
    op.drop_constraint("stale_alert_seconds_range", "app_settings", type_="check")
    op.drop_column("app_settings", "stale_alert_seconds")
