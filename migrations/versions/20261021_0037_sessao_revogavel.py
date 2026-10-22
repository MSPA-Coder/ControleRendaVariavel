"""Sessão revogável no logout (coluna nova, aditiva).

``users.session_epoch`` entra na marca que amarra o cookie à conta (ver
``User.get_id``). O logout soma 1: cookies emitidos antes -- o de sessão e o
"lembrar-me" -- deixam de valer, em vez de continuarem aceitos até expirarem.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261021_0037"
down_revision = "20261021_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "session_epoch")
