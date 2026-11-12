"""Arquivo do extrato de opções encerradas por inteiro (`option_movement_archive`).

O encerramento total de uma ação copia cada movimento para
`position_movement_archive` antes de a cascata apagar a posição; o de uma opção
só alimentava a projeção mínima da série (`position_ledger_archive`). A tabela
das ações não serve às opções: `ticker_id` é NOT NULL e aponta para `tickers`,
e `source_position_id` não distingue a posição de ação da de opção, que têm
sequências de id independentes. Esta tabela espelha a outra trocando
`ticker_id` por `option_contract_id` (FK para `option_contracts`).

Só recebe encerramentos totais feitos depois desta revisão: o extrato dos
anteriores foi apagado em cascata e não há como reconstruí-lo. Nenhum dado
existente é lido ou alterado.

Sem gatilho do outbox do patrimônio v4, ao contrário da tabela das ações: o
recurso `position_ledger` publica só `position_movement_archive` (contrato v4,
ações), então um evento de opção anunciaria algo que o feed não publica.

Idempotente: `IF NOT EXISTS` em tudo. No fim, confere a forma da tabela
(colunas, CHECK, chaves e índices) e falha, desfazendo o DDL, se algo divergir do
esperado. O `downgrade` apaga a tabela.
"""

from __future__ import annotations

from alembic import op

revision = "20261111_0046"
down_revision = "20261111_0045"
branch_labels = None
depends_on = None

TABELA = "option_movement_archive"

COLUNAS = (
    "id", "owner_id", "option_contract_id", "portfolio_id", "broker_id", "side",
    "source_position_id", "source_movement_id", "kind", "quantity_delta", "price",
    "occurred_on", "result", "source_transaction_id", "resulting_quantity",
    "resulting_average_cost", "source_created_at", "archived_at",
)

CHECKS = (
    "kind_valid", "side_valid", "price_non_negative", "resulting_quantity_non_negative",
    "resulting_average_cost_non_negative", "quantity_delta_finite", "price_finite",
    "resulting_quantity_finite", "resulting_average_cost_finite", "result_finite",
    "result_only_on_reduction_or_close", "quantity_delta_sign_matches_kind",
    "transaction_only_on_reduction", "close_event_matches_final_tx",
)

OUTRAS = (
    "pk_option_movement_archive",
    "uq_option_movement_archive_source",
    "fk_option_movement_archive_owner_id_users",
    "fk_option_movement_archive_option_contract_id_option_contracts",
    "fk_option_movement_archive_portfolio_id_portfolios",
    "fk_option_movement_archive_broker_id_brokers",
    "fk_option_movement_archive_portfolio_owner",
)

INDICES = tuple(
    f"ix_option_movement_archive_{coluna}"
    for coluna in ("owner_id", "option_contract_id", "portfolio_id", "broker_id", "occurred_on")
)

NUMERICO_FINITO = "NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)"


def _check(nome: str, expressao: str) -> str:
    return f"CONSTRAINT ck_{TABELA}_{nome} CHECK ({expressao})"


CRIAR_TABELA = f"""
CREATE TABLE IF NOT EXISTS public.{TABELA} (
    id serial NOT NULL,
    owner_id integer NOT NULL,
    option_contract_id integer NOT NULL,
    portfolio_id integer NOT NULL,
    broker_id integer NOT NULL,
    side character varying(4) NOT NULL,
    source_position_id integer NOT NULL,
    source_movement_id integer,
    kind character varying(16) NOT NULL,
    quantity_delta numeric(24,8) NOT NULL,
    price numeric(24,8) NOT NULL,
    occurred_on date NOT NULL,
    result numeric(24,8),
    source_transaction_id integer,
    resulting_quantity numeric(24,8) NOT NULL,
    resulting_average_cost numeric(24,8) NOT NULL,
    source_created_at timestamp with time zone,
    archived_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_{TABELA} PRIMARY KEY (id),
    CONSTRAINT uq_{TABELA}_source UNIQUE (owner_id, source_position_id, source_movement_id),
    CONSTRAINT fk_{TABELA}_owner_id_users
        FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT,
    CONSTRAINT fk_{TABELA}_option_contract_id_option_contracts
        FOREIGN KEY (option_contract_id) REFERENCES public.option_contracts(id) ON DELETE RESTRICT,
    CONSTRAINT fk_{TABELA}_portfolio_id_portfolios
        FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(id) ON DELETE RESTRICT,
    CONSTRAINT fk_{TABELA}_broker_id_brokers
        FOREIGN KEY (broker_id) REFERENCES public.brokers(id) ON DELETE RESTRICT,
    CONSTRAINT fk_{TABELA}_portfolio_owner
        FOREIGN KEY (portfolio_id, owner_id) REFERENCES public.portfolios(id, owner_id) ON DELETE RESTRICT,
    {_check("kind_valid", "kind IN ('open', 'increase', 'decrease', 'adjustment', 'close')")},
    {_check("side_valid", "side IN ('C', 'V')")},
    {_check("price_non_negative", "price >= 0")},
    {_check("resulting_quantity_non_negative", "resulting_quantity >= 0")},
    {_check("resulting_average_cost_non_negative", "resulting_average_cost >= 0")},
    {_check("quantity_delta_finite", f"quantity_delta {NUMERICO_FINITO}")},
    {_check("price_finite", f"price {NUMERICO_FINITO}")},
    {_check("resulting_quantity_finite", f"resulting_quantity {NUMERICO_FINITO}")},
    {_check("resulting_average_cost_finite", f"resulting_average_cost {NUMERICO_FINITO}")},
    {_check("result_finite", f"result IS NULL OR result {NUMERICO_FINITO}")},
    {_check("result_only_on_reduction_or_close", "(kind IN ('decrease', 'close')) = (result IS NOT NULL)")},
    {_check("quantity_delta_sign_matches_kind",
            "(kind IN ('open', 'increase') AND quantity_delta > 0) OR "
            "(kind IN ('decrease', 'close') AND quantity_delta < 0) OR kind = 'adjustment'")},
    {_check("transaction_only_on_reduction", "source_transaction_id IS NULL OR kind IN ('decrease', 'close')")},
    {_check("close_event_matches_final_tx",
            "kind <> 'close' OR (source_transaction_id IS NOT NULL AND source_movement_id IS NULL "
            "AND resulting_quantity = 0)")}
)
"""

def _lista(nomes: tuple[str, ...]) -> str:
    return ", ".join(f"'{nome}'" for nome in nomes)


def upgrade() -> None:
    op.execute(CRIAR_TABELA)
    for indice in INDICES:
        coluna = indice.removeprefix(f"ix_{TABELA}_")
        op.execute(f"CREATE INDEX IF NOT EXISTS {indice} ON public.{TABELA} USING btree ({coluna})")
    # Autoverificação: uma tabela que já existisse com outra forma reprova a
    # revisão inteira, em vez de ficar no meio do caminho.
    op.execute(
        f"""
        DO $$
        DECLARE colunas text; checks text; outras text; indices text;
        BEGIN
            SELECT string_agg(column_name, ',' ORDER BY ordinal_position) INTO colunas
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = '{TABELA}';
            IF colunas IS DISTINCT FROM '{",".join(COLUNAS)}' THEN
                RAISE EXCEPTION '{TABELA}: colunas inesperadas (%)', colunas;
            END IF;
            SELECT string_agg(replace(k.conname, 'ck_{TABELA}_', ''), ',' ORDER BY k.conname) INTO checks
            FROM pg_constraint k WHERE k.conrelid = 'public.{TABELA}'::regclass AND k.contype = 'c';
            IF checks IS DISTINCT FROM (SELECT string_agg(n, ',' ORDER BY 'ck_{TABELA}_' || n)
                                        FROM unnest(ARRAY[{_lista(CHECKS)}]) AS n) THEN
                RAISE EXCEPTION '{TABELA}: CHECK inesperados (%)', checks;
            END IF;
            SELECT string_agg(k.conname, ',' ORDER BY k.conname) INTO outras
            FROM pg_constraint k WHERE k.conrelid = 'public.{TABELA}'::regclass AND k.contype <> 'c';
            IF outras IS DISTINCT FROM (SELECT string_agg(n, ',' ORDER BY n)
                                        FROM unnest(ARRAY[{_lista(OUTRAS)}]) AS n) THEN
                RAISE EXCEPTION '{TABELA}: chaves inesperadas (%)', outras;
            END IF;
            SELECT string_agg(indexname, ',' ORDER BY indexname) INTO indices
            FROM pg_indexes WHERE schemaname = 'public' AND tablename = '{TABELA}'
              AND indexname LIKE 'ix\\_%';
            IF indices IS DISTINCT FROM (SELECT string_agg(n, ',' ORDER BY n)
                                         FROM unnest(ARRAY[{_lista(INDICES)}]) AS n) THEN
                RAISE EXCEPTION '{TABELA}: indices inesperados (%)', indices;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS public.{TABELA}")
