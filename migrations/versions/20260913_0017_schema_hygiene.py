"""Higieniza o schema que antecede o isolamento financeiro."""

from __future__ import annotations

from collections.abc import Iterable

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260913_0017"
down_revision = "20260912_0016"
branch_labels = None
depends_on = None

_POSITION_COLUMNS_TO_REMOVE = (
    "broker",
    "ticker",
    "market",
    "rtd_market_code",
    "currency",
)

_REQUIRED_TIMESTAMPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("users", ("created_at", "updated_at")),
    ("portfolios", ("created_at", "updated_at")),
    ("positions", ("created_at", "updated_at")),
    ("position_movements", ("created_at",)),
    ("option_positions", ("created_at", "updated_at")),
    ("option_position_movements", ("created_at",)),
    ("transactions", ("created_at",)),
    ("dividends", ("created_at",)),
)


def _existing_columns(bind: sa.Connection, table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def _legacy_columns_with_values(bind: sa.Connection, columns: Iterable[str]) -> list[str]:
    present = _existing_columns(bind, "positions")
    populated: list[str] = []
    for column in columns:
        if column not in present:
            continue
        has_value = bind.execute(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM positions WHERE {column} IS NOT NULL)")
        ).scalar_one()
        if has_value:
            populated.append(column)
    return populated


def _backfill_timestamps(bind: sa.Connection, table_name: str, columns: tuple[str, ...]) -> None:
    if len(columns) == 2:
        created_at, updated_at = columns
        bind.execute(
            sa.text(
                f"""
                UPDATE {table_name}
                SET {created_at} = COALESCE({created_at}, {updated_at}, CURRENT_TIMESTAMP),
                    {updated_at} = COALESCE({updated_at}, {created_at}, CURRENT_TIMESTAMP)
                WHERE {created_at} IS NULL OR {updated_at} IS NULL
                """
            )
        )
        return

    (created_at,) = columns
    bind.execute(
        sa.text(
            f"""
            UPDATE {table_name}
            SET {created_at} = COALESCE({created_at}, CURRENT_TIMESTAMP)
            WHERE {created_at} IS NULL
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()

    # As colunas são resíduos de um schema anterior à normalização por IDs.
    # Não há mapeamento seguro para valores restantes: recusar antes de qualquer
    # DDL destrutivo impede apagar fatos que não tenham sido identificados.
    populated = _legacy_columns_with_values(bind, _POSITION_COLUMNS_TO_REMOVE)
    if populated:
        joined = ", ".join(populated)
        raise RuntimeError(
            "A revisão 20260913_0017 encontrou valores nas colunas legadas de "
            f"positions ({joined}). Nenhuma coluna foi removida; revise os dados antes de continuar."
        )

    for table_name, columns in _REQUIRED_TIMESTAMPS:
        existing = _existing_columns(bind, table_name)
        present = tuple(column for column in columns if column in existing)
        if not present:
            continue
        _backfill_timestamps(bind, table_name, present)
        nullable = {
            column["name"]: column["nullable"]
            for column in sa.inspect(bind).get_columns(table_name)
        }
        for column in present:
            if nullable[column]:
                op.alter_column(
                    table_name,
                    column,
                    existing_type=sa.DateTime(timezone=True),
                    nullable=False,
                )

    if bind.execute(sa.text("SELECT to_regclass('ix_positions_ticker') IS NOT NULL")).scalar_one():
        op.drop_index("ix_positions_ticker", table_name="positions")

    existing_positions = _existing_columns(bind, "positions")
    for column in _POSITION_COLUMNS_TO_REMOVE:
        if column in existing_positions:
            op.drop_column("positions", column)


def downgrade() -> None:
    raise RuntimeError("A revisão 20260913_0017 remove resíduos sem reconstrução segura.")
