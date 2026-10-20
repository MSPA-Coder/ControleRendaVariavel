"""Fim da carteira simulada: sai a coluna `portfolios.simulated` e a `simulada` das views.

A carteira "Simulada" nunca serviu ao que se imaginava e foi retirada do
sistema em 20/10/2026. Esta revisão:

1. apaga as carteiras marcadas como simuladas. A FK das posições, transações e
   do arquivo é RESTRICT: se alguma ainda tiver linha, o `DELETE` falha e a
   revisão inteira é desfeita, em vez de apagar dado. Em produção não havia
   nenhuma;
2. recria `leitura.carteira`, `leitura.posicao` e `leitura.operacao` sem a
   coluna `simulada`. O PostgreSQL recusa `DROP COLUMN` de coluna lida por view
   (ver a `20261003_0027`), então as três são derrubadas e recriadas antes. Quem
   lê por fora (FinancasMCP) deixa de ver `simulada`;
3. devolve o `SELECT` ao usuário de leitura `mcp_leitura`, se ele existir: uma
   view recriada perde os privilégios da antiga;
4. remove a coluna.

Revision ID: 20261020_0035
Revises: 20261019_0034
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "20261020_0035"
down_revision = "20261019_0034"
branch_labels = None
depends_on = None

VIEWS = ("carteira", "posicao", "operacao")

NOVAS = [
    """CREATE VIEW leitura.carteira AS
SELECT
    p.id,
    p.owner_id,
    p.name AS carteira,
    p.currency AS moeda,
    p.is_active AS ativa,
    p.description AS descricao
FROM portfolios p""",
    """COMMENT ON VIEW leitura.carteira IS
    'Carteiras.'""",
    """CREATE VIEW leitura.posicao AS
WITH abertas AS (
    SELECT
        'acao' AS instrumento, p.id AS posicao_id, p.owner_id, p.portfolio_id, p.broker_id,
        t.symbol AS ativo, NULL::text AS objeto, NULL::numeric AS strike, NULL::date AS vencimento,
        NULL::text AS tipo_da_opcao, t.market::text AS mercado, t.currency AS moeda,
        p.side::text AS lado, p.quantity, p.average_cost,
        COALESCE(q.last_price, q.previous_close) AS preco, q.observed_at AS cotado_em, p.opened_on
    FROM positions p
    JOIN tickers t ON t.id = p.ticker_id
    LEFT JOIN quotes q ON q.ticker_id = p.ticker_id
    UNION ALL
    SELECT
        'opcao', op.id, op.owner_id, op.portfolio_id, op.broker_id,
        t.symbol, obj.symbol, c.strike, v.exercise_date,
        c.option_type::text, t.market::text, t.currency,
        op.side::text, op.quantity, op.average_cost,
        COALESCE(oq.last_price, oq.previous_close), oq.observed_at, op.opened_on
    FROM option_positions op
    JOIN option_contracts c ON c.id = op.contract_id
    JOIN tickers t ON t.id = c.ticker_id
    JOIN tickers obj ON obj.id = c.underlying_ticker_id
    JOIN option_expirations v ON v.id = c.expiration_id
    LEFT JOIN option_quotes oq ON oq.contract_id = op.contract_id
)
SELECT
    a.instrumento,
    a.posicao_id,
    a.owner_id,
    pf.id AS carteira_id,
    pf.name AS carteira,
    a.broker_id AS corretora_id,
    br.name AS corretora,
    a.ativo,
    a.objeto,
    a.strike,
    a.vencimento,
    a.tipo_da_opcao,
    a.mercado,
    a.moeda,
    a.lado,
    a.quantity AS quantidade,
    a.average_cost AS custo_medio,
    a.quantity * a.average_cost AS custo_total,
    a.preco,
    a.cotado_em,
    CASE WHEN a.lado = 'BUY' THEN 1 ELSE -1 END * a.quantity * a.preco AS valor_mercado,
    CASE WHEN a.lado = 'BUY' THEN 1 ELSE -1 END * (a.preco - a.average_cost) * a.quantity AS resultado_aberto,
    a.opened_on AS aberta_em
FROM abertas a
JOIN portfolios pf ON pf.id = a.portfolio_id
LEFT JOIN brokers br ON br.id = a.broker_id
WHERE a.quantity <> 0""",
    """COMMENT ON VIEW leitura.posicao IS
    'Posições abertas em ações e opções, com custo, última cotação e resultado não realizado. lado BUY é comprada; SELL, vendida (valor_mercado negativo). Não some BRL com USD.'""",
    """CREATE VIEW leitura.operacao AS
SELECT
    tr.id,
    tr.owner_id,
    t.symbol AS ativo,
    t.currency AS moeda,
    tr.side::text AS lado,
    tr.quantity AS quantidade,
    tr.average_cost AS preco_de_entrada,
    tr.exit_price AS preco_de_saida,
    tr.opened_on AS aberta_em,
    tr.closed_on AS fechada_em,
    tr.status::text AS status,
    tr.result AS resultado,
    pf.name AS carteira,
    br.name AS corretora,
    tr.option_contract_id IS NOT NULL AS e_opcao,
    obj.symbol AS objeto,
    tr.notes AS notas
FROM transactions tr
LEFT JOIN option_contracts oc ON oc.id = tr.option_contract_id
LEFT JOIN tickers obj ON obj.id = oc.underlying_ticker_id
JOIN tickers t ON t.id = COALESCE(tr.ticker_id, oc.ticker_id)
LEFT JOIN portfolios pf ON pf.id = tr.portfolio_id
LEFT JOIN brokers br ON br.id = tr.broker_id""",
    """COMMENT ON VIEW leitura.operacao IS
    'Operações de compra e venda de ações e de opções (e_opcao; ativo é o código da opção e objeto, o ativo-objeto). status OPEN ou CLOSED; resultado é o realizado ao fechar, BRUTO (sem custos nem IR). Resultados anteriores a 25/09/2026 podem trazer o fator 0,9996 do modo líquido antigo, sem como distinguir.'""",
]

REGRANT = """DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_leitura') THEN
        GRANT SELECT ON leitura.carteira, leitura.posicao, leitura.operacao TO mcp_leitura;
    END IF;
END
$$"""


def _is_public_schema() -> bool:
    # As views são o contrato do esquema `public`; ver a `20261003_0027`.
    return op.get_bind().exec_driver_sql("SELECT current_schema()").scalar() == "public"


def _drop_views() -> None:
    for nome in VIEWS:
        op.execute(f"DROP VIEW IF EXISTS leitura.{nome}")


def _statements_da_0027() -> list[str]:
    caminho = Path(__file__).with_name("20261003_0027_esquema_leitura.py")
    spec = importlib.util.spec_from_file_location("revisao_0027_esquema_leitura", caminho)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    statements: list[str] = modulo.STATEMENTS
    escolhidas: list[str] = []
    for indice, statement in enumerate(statements):
        if any(statement.startswith(f"CREATE VIEW leitura.{nome} AS") for nome in VIEWS):
            escolhidas.extend([statement, statements[indice + 1]])
    return escolhidas


def upgrade() -> None:
    op.execute("DELETE FROM portfolios WHERE simulated")
    if _is_public_schema():
        _drop_views()
        for statement in NOVAS:
            op.execute(statement)
        op.execute(REGRANT)
    op.drop_column("portfolios", "simulated")


def downgrade() -> None:
    op.add_column(
        "portfolios",
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("portfolios", "simulated", server_default=None)
    if _is_public_schema():
        _drop_views()
        for statement in _statements_da_0027():
            op.execute(statement)
        op.execute(REGRANT)
