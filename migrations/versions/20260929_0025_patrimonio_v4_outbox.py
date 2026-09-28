"""Outbox transacional de invalidação para o contrato patrimônio v4.

Revision ID: 20260929_0025
Revises: 20260928_0024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260929_0025"
down_revision = "20260928_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patrimonio_v4_change_counter",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("id = 1", name="ck_patrimonio_v4_change_counter_singleton"),
        sa.CheckConstraint("value >= 0", name="ck_patrimonio_v4_change_counter_value_non_negative"),
    )
    op.execute("INSERT INTO patrimonio_v4_change_counter (id, value) VALUES (1, 0)")
    op.create_table(
        "patrimonio_v4_outbox",
        sa.Column("cursor", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("resource", sa.String(length=32), nullable=False),
        sa.Column("source_record_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=8), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "resource IN ('holding', 'income', 'price_current', 'price_history', 'position_ledger')",
            name="ck_patrimonio_v4_outbox_resource_valid",
        ),
        sa.CheckConstraint(
            "operation IN ('upsert', 'delete')",
            name="ck_patrimonio_v4_outbox_operation_valid",
        ),
    )
    op.create_index("ix_patrimonio_v4_outbox_owner_id", "patrimonio_v4_outbox", ["owner_id"])
    op.create_index(
        "ix_patrimonio_v4_outbox_owner_cursor",
        "patrimonio_v4_outbox",
        ["owner_id", "cursor"],
    )

    op.execute(
        """
        CREATE FUNCTION patrimonio_v4_outbox_emit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            row_data jsonb;
            next_cursor bigint;
            event_owner integer;
            event_record integer;
        BEGIN
            row_data := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
            event_record := (row_data ->> TG_ARGV[1])::integer;
            event_owner := CASE
                WHEN TG_ARGV[2] = '' THEN NULL
                ELSE (row_data ->> TG_ARGV[2])::integer
            END;
            UPDATE patrimonio_v4_change_counter
            SET value = value + 1
            WHERE id = 1
            RETURNING value INTO next_cursor;
            INSERT INTO patrimonio_v4_outbox (
                cursor, owner_id, resource, source_record_id, operation, changed_at
            ) VALUES (
                next_cursor, event_owner, TG_ARGV[0], event_record,
                CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
                clock_timestamp()
            );
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    for table, resource, record, owner in (
        ("positions", "holding", "id", "owner_id"),
        ("dividends", "income", "id", "owner_id"),
        ("quotes", "price_current", "ticker_id", ""),
        ("quote_history", "price_history", "id", ""),
        ("position_movement_archive", "position_ledger", "id", "owner_id"),
    ):
        op.execute(
            f"CREATE TRIGGER patrimonio_v4_outbox_{table} "
            f"AFTER INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION patrimonio_v4_outbox_emit('{resource}', '{record}', '{owner}')"
        )


def downgrade() -> None:
    for table in (
        "position_movement_archive",
        "quote_history",
        "quotes",
        "dividends",
        "positions",
    ):
        op.execute(f"DROP TRIGGER patrimonio_v4_outbox_{table} ON {table}")
    op.execute("DROP FUNCTION patrimonio_v4_outbox_emit()")
    op.drop_table("patrimonio_v4_outbox")
    op.drop_table("patrimonio_v4_change_counter")
