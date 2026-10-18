"""A chave de importacao dos proventos passa a valer so para a planilha.

A revisao 0022 criou a restricao ``import_key`` (dono, ativo, corretora, tipo,
data, valor) para a importacao da planilha nao duplicar linhas. Com os
proventos do Controle Bancario (revisao 0031) ela atrapalha: dois creditos
distintos do CB, no mesmo dia, do mesmo ativo, tipo e valor, sao dois
lancamentos e nao um duplicado; a identidade deles e ``origin_id``, com indice
proprio. A restricao vira um indice unico parcial, ``WHERE origin = 'planilha'``,
com as mesmas colunas: para a planilha nada muda.

O rollback RECRIA a restricao total e aborta se existirem linhas do CB que a
violem; nesse caso apague as linhas ``origin = 'cb'`` antes (o importador as
recria).
"""

import sqlalchemy as sa
from alembic import op

revision = "20261018_0032"
down_revision = "20261018_0031"
branch_labels = None
depends_on = None

_COLUNAS = ["owner_id", "ticker_id", "broker_id", "kind", "payment_date", "amount"]


def upgrade() -> None:
    op.drop_constraint("import_key", "dividends", type_="unique")
    op.create_index(
        "uq_dividends_import_key_planilha",
        "dividends",
        _COLUNAS,
        unique=True,
        postgresql_where=sa.text("origin = 'planilha'"),
    )


def downgrade() -> None:
    op.drop_index("uq_dividends_import_key_planilha", table_name="dividends")
    op.create_unique_constraint("import_key", "dividends", _COLUNAS)
