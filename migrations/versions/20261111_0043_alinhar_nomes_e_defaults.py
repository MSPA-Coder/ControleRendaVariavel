"""Alinha nomes de CHECK e defaults de colunas entre produção e a base.

A base de 03/11 (`20261103_base.sql`) saiu de um banco montado pela cadeia
antiga, e não do de produção. Medido em 10/11/2026, os dois diferem em duas
coisas, e só nelas:

- 51 CHECK com nome diferente: produção tem o prefixo duplicado pela
  convenção de nomes (`ck_<tabela>_ck_<tabela>_<nome>`, ou o sufixo de hash de
  4 hexadecimais quando o nome passa de 63 caracteres); a base tem o nome
  simples (`ck_<tabela>_<nome>`);
- 5 defaults de coluna que produção tem e a base não.

Esta revisão leva a produção para os nomes da base e a base para os defaults de
produção. Num banco que já está certo não muda nada: o `RENAME CONSTRAINT` só
roda se o nome antigo existir (e o novo, não), e `SET DEFAULT` com o mesmo
valor não altera o catálogo. Num banco montado pela base, só os 5 defaults
aparecem. `RENAME CONSTRAINT` é só metadado.

O bloco final confere o resultado e falha (desfazendo tudo, o DDL do
PostgreSQL é transacional) se sobrar CHECK com prefixo duplicado nas tabelas
desta lista ou se faltar algum nome esperado. As tabelas fora da lista
(`position_ledger_archive`, `position_movement_archive`,
`patrimonio_v4_outbox`, `patrimonio_v4_change_counter` e `users`) têm 21 CHECK
com prefixo duplicado na base e em produção, iguais nos dois: não são
tocados.

Os pares (antigo, novo) vêm do catálogo de produção de 10/11/2026 comparado
com o da base; os de nome com hash foram casados pela definição da restrição.
O `downgrade` devolve os nomes de produção e mantém os defaults, que produção
já tinha antes desta revisão.
"""

from __future__ import annotations

from alembic import op

revision = "20261111_0043"
down_revision = "20261103_0042"
branch_labels = None
depends_on = None

# (tabela, nome em produção, nome na base)
PARES = (
    ("app_settings", "ck_app_settings_ck_app_settings_poll_interval_seconds_range", "ck_app_settings_poll_interval_seconds_range"),
    ("app_settings", "ck_app_settings_ck_app_settings_risk_free_rate_annual_finite", "ck_app_settings_risk_free_rate_annual_finite"),
    ("app_settings", "ck_app_settings_ck_app_settings_singleton", "ck_app_settings_singleton"),
    ("dividends", "ck_dividends_ck_dividends_amount_finite", "ck_dividends_amount_finite"),
    ("option_contracts", "ck_option_contracts_ck_option_contracts_strike_finite", "ck_option_contracts_strike_finite"),
    ("option_contracts", "ck_option_contracts_ck_option_contracts_strike_non_negative", "ck_option_contracts_strike_non_negative"),
    ("option_position_movements", "ck_option_position_movements_ck_option_position_movemen_1cf0", "ck_option_position_movements_quantity_delta_sign_matches_kind"),
    ("option_position_movements", "ck_option_position_movements_ck_option_position_movemen_2daa", "ck_option_position_movements_transaction_only_on_decrease"),
    ("option_position_movements", "ck_option_position_movements_ck_option_position_movemen_47f1", "ck_option_position_movements_resulting_quantity_positive"),
    ("option_position_movements", "ck_option_position_movements_ck_option_position_movemen_6648", "ck_option_position_movements_result_only_on_decrease"),
    ("option_position_movements", "ck_option_position_movements_ck_option_position_movemen_9fc1", "ck_option_position_movements_result_finite"),
    ("option_position_movements", "ck_option_position_movements_ck_option_position_movemen_a048", "ck_option_position_movements_price_non_negative"),
    ("option_position_movements", "ck_option_position_movements_ck_option_position_movemen_abc9", "ck_option_position_movements_resulting_average_cost_finite"),
    ("option_position_movements", "ck_option_position_movements_ck_option_position_movemen_bb6d", "ck_option_position_movements_resulting_average_cost_non_c8db"),
    ("option_position_movements", "ck_option_position_movements_ck_option_position_movemen_d243", "ck_option_position_movements_price_finite"),
    ("option_position_movements", "ck_option_position_movements_ck_option_position_movemen_e04e", "ck_option_position_movements_quantity_delta_finite"),
    ("option_position_movements", "ck_option_position_movements_ck_option_position_movemen_e610", "ck_option_position_movements_resulting_quantity_finite"),
    ("option_positions", "ck_option_positions_ck_option_positions_average_cost_finite", "ck_option_positions_average_cost_finite"),
    ("option_positions", "ck_option_positions_ck_option_positions_average_cost_no_e014", "ck_option_positions_average_cost_non_negative"),
    ("option_positions", "ck_option_positions_ck_option_positions_quantity_finite", "ck_option_positions_quantity_finite"),
    ("option_positions", "ck_option_positions_ck_option_positions_quantity_positive", "ck_option_positions_quantity_positive"),
    ("option_positions", "ck_option_positions_ck_option_positions_target_finite", "ck_option_positions_target_finite"),
    ("option_positions", "ck_option_positions_ck_option_positions_target_non_negative", "ck_option_positions_target_non_negative"),
    ("portfolios", "ck_portfolios_ck_portfolios_currency_valid", "ck_portfolios_currency_valid"),
    ("portfolios", "ck_portfolios_ck_portfolios_name_not_blank", "ck_portfolios_name_not_blank"),
    ("position_movements", "ck_position_movements_ck_position_movements_price_finite", "ck_position_movements_price_finite"),
    ("position_movements", "ck_position_movements_ck_position_movements_price_non_negative", "ck_position_movements_price_non_negative"),
    ("position_movements", "ck_position_movements_ck_position_movements_quantity_de_085f", "ck_position_movements_quantity_delta_sign_matches_kind"),
    ("position_movements", "ck_position_movements_ck_position_movements_quantity_de_6944", "ck_position_movements_quantity_delta_finite"),
    ("position_movements", "ck_position_movements_ck_position_movements_result_finite", "ck_position_movements_result_finite"),
    ("position_movements", "ck_position_movements_ck_position_movements_result_only_cd22", "ck_position_movements_result_only_on_decrease"),
    ("position_movements", "ck_position_movements_ck_position_movements_resulting_a_1622", "ck_position_movements_resulting_average_cost_non_negative"),
    ("position_movements", "ck_position_movements_ck_position_movements_resulting_a_d318", "ck_position_movements_resulting_average_cost_finite"),
    ("position_movements", "ck_position_movements_ck_position_movements_resulting_q_08d7", "ck_position_movements_resulting_quantity_finite"),
    ("position_movements", "ck_position_movements_ck_position_movements_resulting_q_7a9e", "ck_position_movements_resulting_quantity_positive"),
    ("position_movements", "ck_position_movements_ck_position_movements_transaction_d320", "ck_position_movements_transaction_only_on_decrease"),
    ("positions", "ck_positions_ck_positions_average_cost_finite", "ck_positions_average_cost_finite"),
    ("positions", "ck_positions_ck_positions_average_cost_non_negative", "ck_positions_average_cost_non_negative"),
    ("positions", "ck_positions_ck_positions_quantity_finite", "ck_positions_quantity_finite"),
    ("positions", "ck_positions_ck_positions_quantity_positive", "ck_positions_quantity_positive"),
    ("positions", "ck_positions_ck_positions_target_multiplier_finite", "ck_positions_target_multiplier_finite"),
    ("positions", "ck_positions_ck_positions_target_multiplier_positive", "ck_positions_target_multiplier_positive"),
    ("quote_history", "ck_quote_history_ck_quote_history_price_finite", "ck_quote_history_price_finite"),
    ("tickers", "ck_tickers_ck_tickers_currency_valid", "ck_tickers_currency_valid"),
    ("tickers", "ck_tickers_ck_tickers_rtd_market_code_valid", "ck_tickers_rtd_market_code_valid"),
    ("transactions", "ck_transactions_ck_transactions_average_cost_finite", "ck_transactions_average_cost_finite"),
    ("transactions", "ck_transactions_ck_transactions_exactly_one_instrument", "ck_transactions_exactly_one_instrument"),
    ("transactions", "ck_transactions_ck_transactions_exit_price_finite", "ck_transactions_exit_price_finite"),
    ("transactions", "ck_transactions_ck_transactions_quantity_finite", "ck_transactions_quantity_finite"),
    ("transactions", "ck_transactions_ck_transactions_result_finite", "ck_transactions_result_finite"),
    ("transactions", "ck_transactions_ck_transactions_status_fields_consistency", "ck_transactions_status_fields_consistency"),
)

# (tabela, coluna, expressão do default)
DEFAULTS = (
    ("app_settings", "poll_interval_seconds", "2"),
    ("app_settings", "risk_free_rate_annual", "0.1075"),
    ("tickers", "is_benchmark", "false"),
    ("transactions", "status", "'CLOSED'"),
    ("users", "is_active_user", "true"),
)


def _valores() -> str:
    return ", ".join(f"('{t}', '{antigo}', '{novo}')" for t, antigo, novo in PARES)


def _renomear(de: int, para: int) -> None:
    """Renomeia só o que existe com o nome de origem e ainda não com o de destino."""
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT * FROM (VALUES {_valores()}) AS v(tabela, antigo, novo) LOOP
                IF r.{('antigo', 'novo')[de]} IS NOT NULL
                   AND EXISTS (
                        SELECT 1 FROM pg_constraint k
                        JOIN pg_class c ON c.oid = k.conrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public' AND c.relname = r.tabela
                          AND k.contype = 'c' AND k.conname = r.{('antigo', 'novo')[de]})
                   AND NOT EXISTS (
                        SELECT 1 FROM pg_constraint k
                        JOIN pg_class c ON c.oid = k.conrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public' AND c.relname = r.tabela
                          AND k.contype = 'c' AND k.conname = r.{('antigo', 'novo')[para]})
                THEN
                    EXECUTE format('ALTER TABLE public.%I RENAME CONSTRAINT %I TO %I',
                                   r.tabela, r.{('antigo', 'novo')[de]}, r.{('antigo', 'novo')[para]});
                END IF;
            END LOOP;
        END $$
        """
    )


def upgrade() -> None:
    _renomear(0, 1)
    for tabela, coluna, valor in DEFAULTS:
        op.execute(f"ALTER TABLE public.{tabela} ALTER COLUMN {coluna} SET DEFAULT {valor}")
    # Confere o resultado: se algo ficou fora do esperado, a revisão inteira
    # falha e o DDL é desfeito.
    op.execute(
        f"""
        DO $$
        DECLARE sobra text; falta text;
        BEGIN
            SELECT string_agg(c.relname || '.' || k.conname, ', ') INTO sobra
            FROM pg_constraint k
            JOIN pg_class c ON c.oid = k.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND k.contype = 'c'
              AND c.relname IN (SELECT DISTINCT tabela FROM (VALUES {_valores()}) AS p(tabela, antigo, novo))
              AND left(k.conname, length('ck_' || c.relname || '_ck_')) = 'ck_' || c.relname || '_ck_';
            IF sobra IS NOT NULL THEN
                RAISE EXCEPTION 'CHECK com prefixo duplicado depois do alinhamento: %', sobra;
            END IF;
            SELECT string_agg(v.tabela || '.' || v.novo, ', ') INTO falta
            FROM (VALUES {_valores()}) AS v(tabela, antigo, novo)
            WHERE NOT EXISTS (
                SELECT 1 FROM pg_constraint k
                JOIN pg_class c ON c.oid = k.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = v.tabela
                  AND k.contype = 'c' AND k.conname = v.novo);
            IF falta IS NOT NULL THEN
                RAISE EXCEPTION 'CHECK esperado ausente depois do alinhamento: %', falta;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    # Os nomes voltam aos de produção; os defaults ficam, porque produção já os
    # tinha antes desta revisão.
    _renomear(1, 0)
