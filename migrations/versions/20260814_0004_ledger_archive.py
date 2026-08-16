"""Arquivo do extrato de posicoes encerradas.

Encerrar uma posicao por inteiro apaga a linha de `positions`, e o extrato
(`position_movements`) vai junto em cascata. Isso e correto para a Carteira --
o resultado realizado sobrevive em `transactions` --, mas nao para o relatorio
de performance: a serie passou a ser reconstruida do extrato, entao toda
posicao encerrada sumia do historico e o retorno media apenas os
sobreviventes.

Esta tabela e um arquivo somente-adicao, escrito no momento do encerramento e
lido apenas pela reconstrucao da serie. Guarda o minimo para responder "quanto
deste ticker havia nesta data": o saldo resultante de cada lancamento, ja com
o sinal do lado, mais uma linha zerando a posicao na data do encerramento.

Nao substitui `position_movements` nem duplica o extrato exibido na Carteira:
aquele acompanha a posicao viva e continua sendo apagado com ela.

Excluir uma posicao (em vez de encerra-la) continua NAO arquivando nada --
excluir e desfazer, e o que foi desfeito nao deve reaparecer no historico.

Revision ID: 20260814_0004_ledger_archive
Revises: 20260814_0003_income_kind
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# O id fica curto de proposito: `alembic_version.version_num` e varchar(32),
# e um id mais descritivo estoura a coluna com um erro que so aparece no
# fim da migracao, depois de todo o DDL ja ter rodado.
revision = "20260814_0004_ledger_archive"
down_revision = "20260814_0003_income_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_ledger_archive",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurred_on", sa.Date(), nullable=False, index=True),
        sa.Column(
            "ticker_id",
            sa.Integer(),
            sa.ForeignKey("tickers.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "portfolio_id",
            sa.Integer(),
            sa.ForeignKey("portfolios.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "broker_id",
            sa.Integer(),
            sa.ForeignKey("brokers.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("instrument", sa.String(length=6), nullable=False),
        sa.Column("source_position_id", sa.Integer(), nullable=False),
        sa.Column("resulting_signed_quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "instrument IN ('stock', 'option')", name="ck_position_ledger_archive_instrument"
        ),
        sa.CheckConstraint(
            "resulting_signed_quantity NOT IN "
            "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="ck_position_ledger_archive_quantity_finite",
        ),
    )


def downgrade() -> None:
    op.drop_table("position_ledger_archive")
