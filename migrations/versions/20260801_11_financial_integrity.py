"""Protect financial values and make position closure idempotent.

Revision ID: 20260801_11
Revises: 20260731_10
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260801_11"
down_revision = "20260731_10"
branch_labels = None
depends_on = None

_FINITE = "NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)"
_NON_FINITE = "IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)"
_FINITE_COLUMNS = (
    ("app_settings", "risk_free_rate_annual"),
    ("positions", "quantity"),
    ("positions", "average_cost"),
    ("positions", "quote_multiplier"),
    ("positions", "target_multiplier"),
    ("quotes", "last_price"),
    ("quotes", "previous_close"),
    ("option_contracts", "strike"),
    ("option_positions", "quantity"),
    ("option_positions", "average_cost"),
    ("option_positions", "target_price"),
    ("option_quotes", "last_price"),
    ("option_quotes", "previous_close"),
    ("option_quotes", "underlying_price"),
    ("transactions", "quantity"),
    ("transactions", "average_cost"),
    ("transactions", "exit_price"),
    ("transactions", "result"),
    ("dividends", "amount"),
    ("quote_history", "price"),
)
_RESULT_MODE_TABLES = ("positions", "option_positions", "transactions")


def _finite(table: str, column: str) -> None:
    op.create_check_constraint(
        f"ck_{table}_{column}_finite",
        table,
        f"{column} {_FINITE}",
    )


def _assert_legacy_values_are_compatible() -> None:
    bind = op.get_bind()
    for table, column in _FINITE_COLUMNS:
        has_non_finite_value = bind.execute(
            sa.text(f"SELECT 1 FROM {table} WHERE {column} {_NON_FINITE} LIMIT 1")
        ).scalar()
        if has_non_finite_value:
            raise RuntimeError(
                "Cannot apply revision 20260801_11: "
                f"legacy value in {table}.{column} is not finite. Correct it before upgrading."
            )
    for table in _RESULT_MODE_TABLES:
        has_invalid_result_mode = bind.execute(
            sa.text(f"SELECT 1 FROM {table} WHERE result_mode NOT IN ('L', 'B') LIMIT 1")
        ).scalar()
        if has_invalid_result_mode:
            raise RuntimeError(
                "Cannot apply revision 20260801_11: "
                f"legacy value in {table}.result_mode is invalid. Correct it before upgrading."
            )


def upgrade() -> None:
    _assert_legacy_values_are_compatible()
    _finite("app_settings", "risk_free_rate_annual")

    for column in ("quantity", "average_cost", "quote_multiplier", "target_multiplier"):
        _finite("positions", column)
    op.create_check_constraint(
        "ck_positions_result_mode_valid",
        "positions",
        "result_mode IN ('L', 'B')",
    )

    for column in ("last_price", "previous_close"):
        _finite("quotes", column)

    _finite("option_contracts", "strike")

    for column in ("quantity", "average_cost"):
        _finite("option_positions", column)
    op.create_check_constraint(
        "ck_option_positions_target_finite",
        "option_positions",
        "target_price IS NULL OR target_price " + _FINITE,
    )
    op.create_check_constraint(
        "ck_option_positions_result_mode_valid",
        "option_positions",
        "result_mode IN ('L', 'B')",
    )

    for column in ("last_price", "previous_close", "underlying_price"):
        _finite("option_quotes", column)

    for column in ("quantity", "average_cost", "exit_price", "result"):
        _finite("transactions", column)
    op.create_check_constraint(
        "ck_transactions_result_mode_valid",
        "transactions",
        "result_mode IN ('L', 'B')",
    )
    op.add_column("transactions", sa.Column("source_position_id", sa.Integer(), nullable=True))
    op.create_unique_constraint(
        "uq_transactions_source_position_id",
        "transactions",
        ["source_position_id"],
    )

    _finite("dividends", "amount")
    _finite("quote_history", "price")


def downgrade() -> None:
    op.drop_constraint("uq_transactions_source_position_id", "transactions", type_="unique")
    op.drop_column("transactions", "source_position_id")
    op.drop_constraint("ck_transactions_result_mode_valid", "transactions", type_="check")
    for column in ("quantity", "average_cost", "exit_price", "result"):
        op.drop_constraint(f"ck_transactions_{column}_finite", "transactions", type_="check")

    op.drop_constraint("ck_quote_history_price_finite", "quote_history", type_="check")
    op.drop_constraint("ck_dividends_amount_finite", "dividends", type_="check")

    for column in ("last_price", "previous_close", "underlying_price"):
        op.drop_constraint(f"ck_option_quotes_{column}_finite", "option_quotes", type_="check")

    op.drop_constraint(
        "ck_option_positions_result_mode_valid",
        "option_positions",
        type_="check",
    )
    op.drop_constraint("ck_option_positions_target_finite", "option_positions", type_="check")
    for column in ("quantity", "average_cost"):
        op.drop_constraint(
            f"ck_option_positions_{column}_finite",
            "option_positions",
            type_="check",
        )

    op.drop_constraint("ck_option_contracts_strike_finite", "option_contracts", type_="check")

    for column in ("last_price", "previous_close"):
        op.drop_constraint(f"ck_quotes_{column}_finite", "quotes", type_="check")

    op.drop_constraint("ck_positions_result_mode_valid", "positions", type_="check")
    for column in ("quantity", "average_cost", "quote_multiplier", "target_multiplier"):
        op.drop_constraint(f"ck_positions_{column}_finite", "positions", type_="check")

    op.drop_constraint(
        "ck_app_settings_risk_free_rate_annual_finite",
        "app_settings",
        type_="check",
    )
