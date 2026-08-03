"""Add open transactions (Fase A2): status column, nullable close fields
and backfill an open transaction row for every position already registered.

Antes desta migração, ``transactions`` só guardava operações já
encerradas. A partir de agora, toda ``Position`` aberta ganha uma linha
espelhada aqui com ``status='OPEN'`` (ver ``app.position_closure``), para
que a aba Transações possa listar tanto posições abertas quanto
encerradas, filtráveis por status.

Revision ID: 20260802_12
Revises: 20260801_11
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260802_12"
down_revision = "20260801_11"
branch_labels = None
depends_on = None

transaction_status = postgresql.ENUM(
    "OPEN",
    "CLOSED",
    name="transaction_status",
    create_type=False,
)

_STATUS_FIELDS_CONSISTENCY = (
    "(status = 'OPEN' AND closed_on IS NULL AND exit_price IS NULL "
    "AND result IS NULL) OR "
    "(status = 'CLOSED' AND closed_on IS NOT NULL AND exit_price IS NOT NULL "
    "AND result IS NOT NULL)"
)


def _assert_no_orphaned_open_transactions() -> None:
    """Sanity check antes do downgrade: só é seguro apagar as linhas
    ``OPEN`` (e voltar as colunas de fechamento para NOT NULL) se nenhuma
    delas tiver sido editada para representar algo além do espelho
    automático de uma posição ainda aberta."""

    bind = op.get_bind()
    orphaned = bind.execute(
        sa.text(
            "SELECT 1 FROM transactions t "
            "WHERE t.status = 'OPEN' "
            "AND NOT EXISTS (SELECT 1 FROM positions p WHERE p.id = t.source_position_id) "
            "LIMIT 1"
        )
    ).scalar()
    if orphaned:
        raise RuntimeError(
            "Cannot downgrade revision 20260802_12: há transações abertas cuja "
            "posição de origem não existe mais (source_position_id órfão). "
            "Revise-as manualmente antes de reverter."
        )


def upgrade() -> None:
    transaction_status.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "transactions",
        sa.Column(
            "status",
            transaction_status,
            nullable=False,
            server_default="CLOSED",
        ),
    )
    op.alter_column("transactions", "closed_on", nullable=True)
    op.alter_column("transactions", "exit_price", nullable=True)
    op.alter_column("transactions", "result", nullable=True)

    op.drop_constraint("closed_on_not_before_opened_on", "transactions", type_="check")
    op.create_check_constraint(
        "closed_on_not_before_opened_on",
        "transactions",
        "closed_on IS NULL OR closed_on >= opened_on",
    )
    op.drop_constraint("exit_price_non_negative", "transactions", type_="check")
    op.create_check_constraint(
        "exit_price_non_negative",
        "transactions",
        "exit_price IS NULL OR exit_price >= 0",
    )
    op.drop_constraint("ck_transactions_exit_price_finite", "transactions", type_="check")
    op.create_check_constraint(
        "ck_transactions_exit_price_finite",
        "transactions",
        "exit_price IS NULL OR exit_price NOT IN "
        "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
    )
    op.drop_constraint("ck_transactions_result_finite", "transactions", type_="check")
    op.create_check_constraint(
        "ck_transactions_result_finite",
        "transactions",
        "result IS NULL OR result NOT IN "
        "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
    )
    op.create_check_constraint(
        "ck_transactions_status_fields_consistency",
        "transactions",
        _STATUS_FIELDS_CONSISTENCY,
    )

    # Backfill: toda posição já cadastrada (aberta, por definição — não há
    # tabela de posições encerradas) ganha uma linha espelhada aqui com os
    # valores já cadastrados, para aparecer em Transações como aberta.
    op.execute(
        sa.text(
            "INSERT INTO transactions "
            "(broker_id, ticker_id, quantity, average_cost, exit_price, side, "
            " opened_on, closed_on, result_mode, result, status, position_kind, "
            " source_position_id, notes, created_at) "
            "SELECT broker_id, ticker_id, quantity, average_cost, NULL, side, "
            " opened_on, NULL, result_mode, NULL, 'OPEN', position_kind, "
            " id, NULL, now() "
            "FROM positions"
        )
    )


def downgrade() -> None:
    _assert_no_orphaned_open_transactions()
    op.execute(sa.text("DELETE FROM transactions WHERE status = 'OPEN'"))

    op.drop_constraint("ck_transactions_status_fields_consistency", "transactions", type_="check")
    op.drop_constraint("ck_transactions_result_finite", "transactions", type_="check")
    op.create_check_constraint(
        "ck_transactions_result_finite",
        "transactions",
        "result NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
    )
    op.drop_constraint("ck_transactions_exit_price_finite", "transactions", type_="check")
    op.create_check_constraint(
        "ck_transactions_exit_price_finite",
        "transactions",
        "exit_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
    )
    op.drop_constraint("exit_price_non_negative", "transactions", type_="check")
    op.create_check_constraint("exit_price_non_negative", "transactions", "exit_price >= 0")
    op.drop_constraint("closed_on_not_before_opened_on", "transactions", type_="check")
    op.create_check_constraint(
        "closed_on_not_before_opened_on", "transactions", "closed_on >= opened_on"
    )

    op.alter_column("transactions", "result", nullable=False)
    op.alter_column("transactions", "exit_price", nullable=False)
    op.alter_column("transactions", "closed_on", nullable=False)
    op.drop_column("transactions", "status")

    transaction_status.drop(op.get_bind(), checkfirst=True)
