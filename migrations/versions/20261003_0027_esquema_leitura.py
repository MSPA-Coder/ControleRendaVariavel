"""Publishes the `leitura` schema: versioned views for readers outside the app.

Revision ID: 20261003_0027
Revises: 20260929_0026

Whoever reads these tables directly (today, the FinancasMCP) couples its SQL to
the schema and re-implements the domain rules: which positions are open, the
price that counts, the sign of a short position. On 2026-09-24 a removed column
broke `crv_carteira` in production. The views below carry those rules once, and
the reader gets SELECT on them only, not on the tables.

PostgreSQL refuses `DROP COLUMN` and `ALTER ... TYPE` on a column a view reads,
so a schema change that would break the reader fails the author's migration
(the suite applies every revision to an empty database), not the reader in
production. Whoever changes a column read here must depend on this revision and
recreate the view.
"""

from __future__ import annotations

from alembic import op

revision = "20261003_0027"
down_revision = "20260929_0026"
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE SCHEMA leitura",
    """COMMENT ON SCHEMA leitura IS
    'Contrato de leitura do CRV: views versionadas pelas migrações. Quem lê o banco de fora usa só este esquema.'""",
    # Reference data.
    """CREATE VIEW leitura.ativo AS
SELECT
    t.id,
    t.symbol AS ativo,
    t.trading_name AS nome,
    t.market::text AS mercado,
    t.currency AS moeda,
    t.is_benchmark AS e_referencia,
    t.is_active AS ativa
FROM tickers t""",
    """COMMENT ON VIEW leitura.ativo IS
    'Ativos (ações, opções e referências). mercado: B3, NYSE ou NASDAQ. Não some moedas diferentes.'""",
    """CREATE VIEW leitura.corretora AS
SELECT b.id, b.name AS corretora, b.acronym AS sigla, b.is_active AS ativa
FROM brokers b""",
    """CREATE VIEW leitura.carteira AS
SELECT
    p.id,
    p.owner_id,
    p.name AS carteira,
    p.currency AS moeda,
    p.simulated AS simulada,
    p.is_active AS ativa,
    p.description AS descricao
FROM portfolios p""",
    """COMMENT ON VIEW leitura.carteira IS
    'Carteiras. simulada = true é carteira simulada: exclua de análises reais.'""",
    """CREATE VIEW leitura.carteira_ativo AS
SELECT pt.portfolio_id AS carteira_id, t.symbol AS ativo
FROM portfolio_tickers pt
JOIN tickers t ON t.id = pt.ticker_id""",
    # Open positions, stocks and options together: the complete portfolio is both.
    #   preco            last quote, or the previous close when there is none.
    #   valor_mercado    quantidade x preco, negative for a short (lado SELL).
    #   resultado_aberto unrealized result, with the same sign rule.
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
    pf.simulated AS simulada,
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
    'Posições abertas em ações e opções, com custo, última cotação e resultado não realizado. lado BUY é comprada; SELL, vendida (valor_mercado negativo). Exclua simulada = true das análises reais. Não some BRL com USD.'""",
    """CREATE VIEW leitura.cotacao AS
SELECT
    t.symbol AS ativo,
    t.market::text AS mercado,
    t.currency AS moeda,
    q.last_price AS ultimo_preco,
    q.previous_close AS fechamento_anterior,
    COALESCE(q.last_price, q.previous_close) AS preco,
    q.buy_price AS preco_de_compra,
    q.sell_price AS preco_de_venda,
    q.observed_at AS cotado_em,
    q.instrument_status AS status_do_instrumento,
    q.source_status AS status_da_fonte,
    q.error_message AS mensagem_de_erro
FROM quotes q
JOIN tickers t ON t.id = q.ticker_id""",
    """COMMENT ON VIEW leitura.cotacao IS
    'Última cotação de cada ativo. preco = último preço, ou o fechamento anterior quando não há. cotado_em mostra a idade do dado.'""",
    """CREATE VIEW leitura.cotacao_historico AS
SELECT h.id, t.symbol AS ativo, h.price AS preco, h.recorded_date AS data, h.recorded_at AS registrada_em
FROM quote_history h
JOIN tickers t ON t.id = h.ticker_id""",
    """CREATE VIEW leitura.opcao AS
SELECT
    c.id,
    t.symbol AS ativo,
    obj.symbol AS objeto,
    c.option_type::text AS tipo,
    c.strike,
    v.exercise_date AS vencimento,
    v.call_code AS codigo_da_call,
    v.put_code AS codigo_da_put
FROM option_contracts c
JOIN tickers t ON t.id = c.ticker_id
JOIN tickers obj ON obj.id = c.underlying_ticker_id
JOIN option_expirations v ON v.id = c.expiration_id""",
    """CREATE VIEW leitura.cotacao_opcao AS
SELECT
    c.id AS opcao_id,
    t.symbol AS ativo,
    oq.last_price AS ultimo_preco,
    oq.previous_close AS fechamento_anterior,
    COALESCE(oq.last_price, oq.previous_close) AS preco,
    oq.underlying_price AS preco_do_objeto,
    oq.observed_at AS cotado_em,
    oq.instrument_status AS status_do_instrumento,
    oq.source_status AS status_da_fonte
FROM option_quotes oq
JOIN option_contracts c ON c.id = oq.contract_id
JOIN tickers t ON t.id = c.ticker_id""",
    # Movements: the statement of each position.
    """CREATE VIEW leitura.movimento_posicao AS
SELECT
    m.id,
    m.position_id AS posicao_id,
    m.owner_id,
    pf.name AS carteira,
    br.name AS corretora,
    t.symbol AS ativo,
    m.kind::text AS tipo,
    m.quantity_delta AS variacao_da_quantidade,
    m.price AS preco,
    m.occurred_on AS data,
    m.result AS resultado,
    m.resulting_quantity AS quantidade_resultante,
    m.resulting_average_cost AS custo_medio_resultante,
    m.transaction_id AS operacao_id,
    m.created_at AS criado_em
FROM position_movements m
LEFT JOIN positions p ON p.id = m.position_id
LEFT JOIN tickers t ON t.id = p.ticker_id
LEFT JOIN portfolios pf ON pf.id = p.portfolio_id
LEFT JOIN brokers br ON br.id = p.broker_id""",
    """COMMENT ON VIEW leitura.movimento_posicao IS
    'Extrato de cada posição em ações: OPEN, INCREASE, DECREASE, ADJUSTMENT. resultado é BRUTO (sem custos nem IR).'""",
    """CREATE VIEW leitura.movimento_opcao AS
SELECT
    m.id,
    m.option_position_id AS posicao_id,
    m.owner_id,
    pf.name AS carteira,
    br.name AS corretora,
    t.symbol AS ativo,
    m.kind::text AS tipo,
    m.quantity_delta AS variacao_da_quantidade,
    m.price AS preco,
    m.occurred_on AS data,
    m.result AS resultado,
    m.resulting_quantity AS quantidade_resultante,
    m.resulting_average_cost AS custo_medio_resultante,
    m.transaction_id AS operacao_id,
    m.created_at AS criado_em
FROM option_position_movements m
LEFT JOIN option_positions op ON op.id = m.option_position_id
LEFT JOIN option_contracts c ON c.id = op.contract_id
LEFT JOIN tickers t ON t.id = c.ticker_id
LEFT JOIN portfolios pf ON pf.id = op.portfolio_id
LEFT JOIN brokers br ON br.id = op.broker_id""",
    """CREATE VIEW leitura.posicao_historica AS
SELECT
    a.id,
    a.owner_id,
    a.occurred_on AS data,
    t.symbol AS ativo,
    pf.name AS carteira,
    br.name AS corretora,
    a.instrument AS instrumento,
    a.resulting_signed_quantity AS quantidade_assinada
FROM position_ledger_archive a
JOIN tickers t ON t.id = a.ticker_id
LEFT JOIN portfolios pf ON pf.id = a.portfolio_id
LEFT JOIN brokers br ON br.id = a.broker_id""",
    # Realized trades and income.
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
    pf.simulated AS simulada,
    br.name AS corretora,
    tr.option_contract_id IS NOT NULL AS e_opcao,
    tr.notes AS notas
FROM transactions tr
JOIN tickers t ON t.id = tr.ticker_id
LEFT JOIN portfolios pf ON pf.id = tr.portfolio_id
LEFT JOIN brokers br ON br.id = tr.broker_id""",
    """COMMENT ON VIEW leitura.operacao IS
    'Operações de compra e venda. status OPEN ou CLOSED; resultado é o realizado ao fechar, BRUTO (sem custos nem IR). Resultados anteriores a 25/09/2026 podem trazer o fator 0,9996 do modo líquido antigo, sem como distinguir.'""",
    """CREATE VIEW leitura.provento AS
SELECT
    d.id,
    d.owner_id,
    t.symbol AS ativo,
    t.currency AS moeda,
    br.name AS corretora,
    d.kind::text AS tipo,
    d.amount AS valor,
    d.payment_date AS pago_em,
    d.com_date AS data_com,
    d.quotas AS cotas,
    d.amount_per_share AS valor_por_cota,
    d.yield_on_cost,
    d.dividend_yield,
    d.invested_total AS investido,
    d.market_total AS valor_de_mercado,
    d.average_price AS preco_medio,
    d.quoted_price AS preco_cotado,
    d.notes AS notas
FROM dividends d
JOIN tickers t ON t.id = d.ticker_id
LEFT JOIN brokers br ON br.id = d.broker_id""",
    """COMMENT ON VIEW leitura.provento IS
    'Proventos recebidos. tipo: DIVIDENDO, JCP ou ALUGUEL; valor na moeda do ativo. yield_on_cost e dividend_yield estão em PONTOS PERCENTUAIS (0,42 = 0,42%). Os campos opcionais são nulos quando não informados.'""",
]


def _is_public_schema() -> bool:
    # The views are the contract of the `public` schema, the one the app and the
    # readers use. Some migration tests walk the chain to head inside throwaway
    # schemas of the SAME database (search_path pointed at them); creating the
    # views there would bind them to the throwaway tables and collide with the
    # real ones, and dropping the throwaway schema would take them down.
    return op.get_bind().exec_driver_sql("SELECT current_schema()").scalar() == "public"


def upgrade() -> None:
    if not _is_public_schema():
        return
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    if not _is_public_schema():
        return
    op.execute("DROP SCHEMA IF EXISTS leitura CASCADE")
