-- Base do schema do ControleRendaVariavel: o banco de produção em 03/11/2026.
-- Gerada por pg_dump do banco que as 42 revisões antigas montavam (estrutura
-- e as linhas que elas semeavam). Não se edita: mudança de schema vai numa
-- revisão nova. Ver 20261103_base.py.

CREATE SCHEMA leitura;

COMMENT ON SCHEMA leitura IS 'Contrato de leitura do CRV: views versionadas pelas migrações. Quem lê o banco de fora usa só este esquema.';

CREATE TYPE public.income_kind AS ENUM (
    'DIVIDENDO',
    'JCP',
    'ALUGUEL',
    'CUPOM'
);

CREATE TYPE public.market AS ENUM (
    'B3',
    'NYSE',
    'NASDAQ'
);

CREATE TYPE public.option_type AS ENUM (
    'CALL',
    'PUT'
);

CREATE TYPE public.position_movement_kind AS ENUM (
    'OPEN',
    'INCREASE',
    'DECREASE',
    'ADJUSTMENT'
);

CREATE TYPE public.position_side AS ENUM (
    'BUY',
    'SELL'
);

CREATE TYPE public.transaction_status AS ENUM (
    'OPEN',
    'CLOSED'
);

CREATE FUNCTION public.incrementar_versao_posicao() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            NEW.edit_version := OLD.edit_version + 1;
            RETURN NEW;
        END;
        $$;

CREATE FUNCTION public.patrimonio_v4_outbox_emit() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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

CREATE TABLE public.tickers (
    id integer NOT NULL,
    symbol character varying(24) NOT NULL,
    trading_name character varying(80) NOT NULL,
    market public.market NOT NULL,
    rtd_market_code character varying(1) NOT NULL,
    currency character varying(3) NOT NULL,
    is_benchmark boolean NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    quote_factor integer DEFAULT 1 NOT NULL,
    CONSTRAINT ck_tickers_currency_valid CHECK ((currency IN ('BRL', 'USD'))),
    CONSTRAINT ck_tickers_quote_factor_positive CHECK ((quote_factor >= 1)),
    CONSTRAINT ck_tickers_rtd_market_code_valid CHECK ((rtd_market_code IN ('B', 'Y', 'N')))
);

CREATE VIEW leitura.ativo AS
 SELECT id,
    symbol AS ativo,
    trading_name AS nome,
    (market)::text AS mercado,
    currency AS moeda,
    is_benchmark AS e_referencia,
    is_active AS ativa
   FROM public.tickers t;

COMMENT ON VIEW leitura.ativo IS 'Ativos (ações, opções e referências). mercado: B3, NYSE ou NASDAQ. Não some moedas diferentes.';

CREATE TABLE public.portfolios (
    id integer NOT NULL,
    name character varying(80) NOT NULL,
    currency character varying(3),
    description character varying(500),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    owner_id integer NOT NULL,
    CONSTRAINT ck_portfolios_currency_valid CHECK (((currency IS NULL) OR (currency IN ('BRL', 'USD')))),
    CONSTRAINT ck_portfolios_name_not_blank CHECK ((length(btrim((name)::text)) > 0))
);

CREATE VIEW leitura.carteira AS
 SELECT id,
    owner_id,
    name AS carteira,
    currency AS moeda,
    is_active AS ativa,
    description AS descricao
   FROM public.portfolios p;

COMMENT ON VIEW leitura.carteira IS 'Carteiras.';

CREATE TABLE public.portfolio_tickers (
    portfolio_id integer NOT NULL,
    ticker_id integer NOT NULL
);

CREATE VIEW leitura.carteira_ativo AS
 SELECT pt.portfolio_id AS carteira_id,
    t.symbol AS ativo
   FROM (public.portfolio_tickers pt
     JOIN public.tickers t ON ((t.id = pt.ticker_id)));

CREATE TABLE public.brokers (
    id integer NOT NULL,
    name character varying(40) NOT NULL,
    acronym character varying(40) NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);

CREATE VIEW leitura.corretora AS
 SELECT id,
    name AS corretora,
    acronym AS sigla,
    is_active AS ativa
   FROM public.brokers b;

CREATE TABLE public.quotes (
    ticker_id integer NOT NULL,
    last_price numeric(24,8) NOT NULL,
    buy_price numeric(24,8),
    sell_price numeric(24,8),
    buy_observed_at timestamp with time zone,
    sell_observed_at timestamp with time zone,
    previous_close numeric(24,8) NOT NULL,
    instrument_status character varying(16) NOT NULL,
    source_status character varying(16) NOT NULL,
    error_message character varying(250),
    observed_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_quotes_new_ck_quotes_buy_price_valid CHECK (((buy_price IS NULL) OR ((buy_price >= (0)::numeric) AND (buy_price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))))),
    CONSTRAINT ck_quotes_new_ck_quotes_last_price_finite CHECK ((last_price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_quotes_new_ck_quotes_last_price_non_negative CHECK ((last_price >= (0)::numeric)),
    CONSTRAINT ck_quotes_new_ck_quotes_previous_close_finite CHECK ((previous_close <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_quotes_new_ck_quotes_previous_close_non_negative CHECK ((previous_close >= (0)::numeric)),
    CONSTRAINT ck_quotes_new_ck_quotes_sell_price_valid CHECK (((sell_price IS NULL) OR ((sell_price >= (0)::numeric) AND (sell_price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))))
);

CREATE VIEW leitura.cotacao AS
 SELECT t.symbol AS ativo,
    (t.market)::text AS mercado,
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
   FROM (public.quotes q
     JOIN public.tickers t ON ((t.id = q.ticker_id)));

COMMENT ON VIEW leitura.cotacao IS 'Última cotação de cada ativo. preco = último preço, ou o fechamento anterior quando não há. cotado_em mostra a idade do dado.';

CREATE TABLE public.quote_history (
    id integer NOT NULL,
    ticker_id integer NOT NULL,
    price numeric(24,8) NOT NULL,
    recorded_date date NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_quote_history_price_finite CHECK ((price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_quote_history_price_non_negative CHECK ((price >= (0)::numeric))
);

CREATE VIEW leitura.cotacao_historico AS
 SELECT h.id,
    t.symbol AS ativo,
    h.price AS preco,
    h.recorded_date AS data,
    h.recorded_at AS registrada_em
   FROM (public.quote_history h
     JOIN public.tickers t ON ((t.id = h.ticker_id)));

CREATE TABLE public.option_contracts (
    id integer NOT NULL,
    ticker_id integer NOT NULL,
    underlying_ticker_id integer NOT NULL,
    expiration_id integer NOT NULL,
    option_type public.option_type NOT NULL,
    strike numeric(24,8) NOT NULL,
    CONSTRAINT ck_option_contracts_strike_finite CHECK ((strike <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_option_contracts_strike_non_negative CHECK ((strike >= (0)::numeric))
);

CREATE TABLE public.option_quotes (
    contract_id integer NOT NULL,
    last_price numeric(24,8) NOT NULL,
    previous_close numeric(24,8) NOT NULL,
    underlying_price numeric(24,8) NOT NULL,
    instrument_status character varying(16) NOT NULL,
    source_status character varying(16) NOT NULL,
    error_message character varying(250),
    observed_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_option_quotes_new_ck_option_quotes_last_price_finite CHECK ((last_price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_option_quotes_new_ck_option_quotes_last_price_non_negative CHECK ((last_price >= (0)::numeric)),
    CONSTRAINT ck_option_quotes_new_ck_option_quotes_previous_close_finite CHECK ((previous_close <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_option_quotes_new_ck_option_quotes_previous_close_no_9fc5 CHECK ((previous_close >= (0)::numeric)),
    CONSTRAINT ck_option_quotes_new_ck_option_quotes_underlying_price__6ae5 CHECK ((underlying_price >= (0)::numeric)),
    CONSTRAINT ck_option_quotes_new_ck_option_quotes_underlying_price_finite CHECK ((underlying_price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))
);

CREATE VIEW leitura.cotacao_opcao AS
 SELECT c.id AS opcao_id,
    t.symbol AS ativo,
    oq.last_price AS ultimo_preco,
    oq.previous_close AS fechamento_anterior,
    COALESCE(oq.last_price, oq.previous_close) AS preco,
    oq.underlying_price AS preco_do_objeto,
    oq.observed_at AS cotado_em,
    oq.instrument_status AS status_do_instrumento,
    oq.source_status AS status_da_fonte
   FROM ((public.option_quotes oq
     JOIN public.option_contracts c ON ((c.id = oq.contract_id)))
     JOIN public.tickers t ON ((t.id = c.ticker_id)));

CREATE TABLE public.option_position_movements (
    id integer NOT NULL,
    option_position_id integer NOT NULL,
    kind public.position_movement_kind NOT NULL,
    quantity_delta numeric(24,8) NOT NULL,
    price numeric(24,8) NOT NULL,
    occurred_on date NOT NULL,
    result numeric(24,8),
    transaction_id integer,
    resulting_quantity numeric(24,8) NOT NULL,
    resulting_average_cost numeric(24,8) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    owner_id integer NOT NULL,
    CONSTRAINT ck_option_position_movements_price_finite CHECK ((price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_option_position_movements_price_non_negative CHECK ((price >= (0)::numeric)),
    CONSTRAINT ck_option_position_movements_quantity_delta_finite CHECK ((quantity_delta <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_option_position_movements_quantity_delta_sign_matches_kind CHECK ((((kind = ANY (ARRAY['OPEN'::public.position_movement_kind, 'INCREASE'::public.position_movement_kind])) AND (quantity_delta > (0)::numeric)) OR ((kind = 'DECREASE'::public.position_movement_kind) AND (quantity_delta < (0)::numeric)) OR (kind = 'ADJUSTMENT'::public.position_movement_kind))),
    CONSTRAINT ck_option_position_movements_result_finite CHECK (((result IS NULL) OR (result <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))),
    CONSTRAINT ck_option_position_movements_result_only_on_decrease CHECK (((kind = 'DECREASE'::public.position_movement_kind) = (result IS NOT NULL))),
    CONSTRAINT ck_option_position_movements_resulting_average_cost_finite CHECK ((resulting_average_cost <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_option_position_movements_resulting_average_cost_non_c8db CHECK ((resulting_average_cost >= (0)::numeric)),
    CONSTRAINT ck_option_position_movements_resulting_quantity_finite CHECK ((resulting_quantity <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_option_position_movements_resulting_quantity_positive CHECK ((resulting_quantity > (0)::numeric)),
    CONSTRAINT ck_option_position_movements_transaction_only_on_decrease CHECK (((transaction_id IS NULL) OR (kind = 'DECREASE'::public.position_movement_kind)))
);

CREATE TABLE public.option_positions (
    id integer NOT NULL,
    broker_id integer NOT NULL,
    contract_id integer NOT NULL,
    quantity numeric(24,8) NOT NULL,
    average_cost numeric(24,8) NOT NULL,
    target_price numeric(24,8),
    side public.position_side NOT NULL,
    opened_on date NOT NULL,
    portfolio_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    owner_id integer NOT NULL,
    edit_version bigint DEFAULT 1 NOT NULL,
    CONSTRAINT ck_option_positions_average_cost_finite CHECK ((average_cost <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_option_positions_average_cost_non_negative CHECK ((average_cost >= (0)::numeric)),
    CONSTRAINT ck_option_positions_quantity_finite CHECK ((quantity <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_option_positions_quantity_positive CHECK ((quantity > (0)::numeric)),
    CONSTRAINT ck_option_positions_target_finite CHECK (((target_price IS NULL) OR (target_price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))),
    CONSTRAINT ck_option_positions_target_non_negative CHECK (((target_price IS NULL) OR (target_price >= (0)::numeric)))
);

CREATE VIEW leitura.movimento_opcao AS
 SELECT m.id,
    m.option_position_id AS posicao_id,
    m.owner_id,
    pf.name AS carteira,
    br.name AS corretora,
    t.symbol AS ativo,
    (m.kind)::text AS tipo,
    m.quantity_delta AS variacao_da_quantidade,
    m.price AS preco,
    m.occurred_on AS data,
    m.result AS resultado,
    m.resulting_quantity AS quantidade_resultante,
    m.resulting_average_cost AS custo_medio_resultante,
    m.transaction_id AS operacao_id,
    m.created_at AS criado_em
   FROM (((((public.option_position_movements m
     LEFT JOIN public.option_positions op ON ((op.id = m.option_position_id)))
     LEFT JOIN public.option_contracts c ON ((c.id = op.contract_id)))
     LEFT JOIN public.tickers t ON ((t.id = c.ticker_id)))
     LEFT JOIN public.portfolios pf ON ((pf.id = op.portfolio_id)))
     LEFT JOIN public.brokers br ON ((br.id = op.broker_id)));

CREATE TABLE public.position_movements (
    id integer NOT NULL,
    position_id integer NOT NULL,
    kind public.position_movement_kind NOT NULL,
    quantity_delta numeric(24,8) NOT NULL,
    price numeric(24,8) NOT NULL,
    occurred_on date NOT NULL,
    result numeric(24,8),
    transaction_id integer,
    resulting_quantity numeric(24,8) NOT NULL,
    resulting_average_cost numeric(24,8) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    owner_id integer NOT NULL,
    CONSTRAINT ck_position_movements_price_finite CHECK ((price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_position_movements_price_non_negative CHECK ((price >= (0)::numeric)),
    CONSTRAINT ck_position_movements_quantity_delta_finite CHECK ((quantity_delta <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_position_movements_quantity_delta_sign_matches_kind CHECK ((((kind = ANY (ARRAY['OPEN'::public.position_movement_kind, 'INCREASE'::public.position_movement_kind])) AND (quantity_delta > (0)::numeric)) OR ((kind = 'DECREASE'::public.position_movement_kind) AND (quantity_delta < (0)::numeric)) OR (kind = 'ADJUSTMENT'::public.position_movement_kind))),
    CONSTRAINT ck_position_movements_result_finite CHECK (((result IS NULL) OR (result <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))),
    CONSTRAINT ck_position_movements_result_only_on_decrease CHECK (((kind = 'DECREASE'::public.position_movement_kind) = (result IS NOT NULL))),
    CONSTRAINT ck_position_movements_resulting_average_cost_finite CHECK ((resulting_average_cost <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_position_movements_resulting_average_cost_non_negative CHECK ((resulting_average_cost >= (0)::numeric)),
    CONSTRAINT ck_position_movements_resulting_quantity_finite CHECK ((resulting_quantity <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_position_movements_resulting_quantity_positive CHECK ((resulting_quantity > (0)::numeric)),
    CONSTRAINT ck_position_movements_transaction_only_on_decrease CHECK (((transaction_id IS NULL) OR (kind = 'DECREASE'::public.position_movement_kind)))
);

CREATE TABLE public.positions (
    id integer NOT NULL,
    broker_id integer NOT NULL,
    ticker_id integer NOT NULL,
    quantity numeric(24,8) NOT NULL,
    average_cost numeric(24,8) NOT NULL,
    side public.position_side NOT NULL,
    opened_on date NOT NULL,
    target_multiplier numeric(18,8) NOT NULL,
    portfolio_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    owner_id integer NOT NULL,
    edit_version bigint DEFAULT 1 NOT NULL,
    CONSTRAINT ck_positions_average_cost_finite CHECK ((average_cost <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_positions_average_cost_non_negative CHECK ((average_cost >= (0)::numeric)),
    CONSTRAINT ck_positions_quantity_finite CHECK ((quantity <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_positions_quantity_positive CHECK ((quantity > (0)::numeric)),
    CONSTRAINT ck_positions_target_multiplier_finite CHECK ((target_multiplier <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_positions_target_multiplier_positive CHECK ((target_multiplier > (0)::numeric))
);

CREATE VIEW leitura.movimento_posicao AS
 SELECT m.id,
    m.position_id AS posicao_id,
    m.owner_id,
    pf.name AS carteira,
    br.name AS corretora,
    t.symbol AS ativo,
    (m.kind)::text AS tipo,
    m.quantity_delta AS variacao_da_quantidade,
    m.price AS preco,
    m.occurred_on AS data,
    m.result AS resultado,
    m.resulting_quantity AS quantidade_resultante,
    m.resulting_average_cost AS custo_medio_resultante,
    m.transaction_id AS operacao_id,
    m.created_at AS criado_em
   FROM ((((public.position_movements m
     LEFT JOIN public.positions p ON ((p.id = m.position_id)))
     LEFT JOIN public.tickers t ON ((t.id = p.ticker_id)))
     LEFT JOIN public.portfolios pf ON ((pf.id = p.portfolio_id)))
     LEFT JOIN public.brokers br ON ((br.id = p.broker_id)));

COMMENT ON VIEW leitura.movimento_posicao IS 'Extrato de cada posição em ações: OPEN, INCREASE, DECREASE, ADJUSTMENT. resultado é BRUTO (sem custos nem IR).';

CREATE TABLE public.option_expirations (
    id integer NOT NULL,
    call_code character varying(5) NOT NULL,
    put_code character varying(5) NOT NULL,
    exercise_date date NOT NULL
);

CREATE VIEW leitura.opcao AS
 SELECT c.id,
    t.symbol AS ativo,
    obj.symbol AS objeto,
    (c.option_type)::text AS tipo,
    c.strike,
    v.exercise_date AS vencimento,
    v.call_code AS codigo_da_call,
    v.put_code AS codigo_da_put
   FROM (((public.option_contracts c
     JOIN public.tickers t ON ((t.id = c.ticker_id)))
     JOIN public.tickers obj ON ((obj.id = c.underlying_ticker_id)))
     JOIN public.option_expirations v ON ((v.id = c.expiration_id)));

CREATE TABLE public.transactions (
    id integer NOT NULL,
    broker_id integer NOT NULL,
    ticker_id integer,
    option_contract_id integer,
    quantity numeric(24,8) NOT NULL,
    average_cost numeric(24,8) NOT NULL,
    exit_price numeric(24,8),
    side public.position_side NOT NULL,
    opened_on date NOT NULL,
    closed_on date,
    result numeric(24,8),
    status public.transaction_status NOT NULL,
    portfolio_id integer NOT NULL,
    source_position_id integer,
    notes character varying(500),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    owner_id integer NOT NULL,
    CONSTRAINT ck_transactions_average_cost_finite CHECK ((average_cost <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_transactions_average_cost_non_negative CHECK ((average_cost >= (0)::numeric)),
    CONSTRAINT ck_transactions_closed_on_not_before_opened_on CHECK (((closed_on IS NULL) OR (closed_on >= opened_on))),
    CONSTRAINT ck_transactions_exactly_one_instrument CHECK ((num_nonnulls(ticker_id, option_contract_id) = 1)),
    CONSTRAINT ck_transactions_exit_price_finite CHECK (((exit_price IS NULL) OR (exit_price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))),
    CONSTRAINT ck_transactions_exit_price_non_negative CHECK (((exit_price IS NULL) OR (exit_price >= (0)::numeric))),
    CONSTRAINT ck_transactions_quantity_finite CHECK ((quantity <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_transactions_quantity_positive CHECK ((quantity > (0)::numeric)),
    CONSTRAINT ck_transactions_result_finite CHECK (((result IS NULL) OR (result <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))),
    CONSTRAINT ck_transactions_status_fields_consistency CHECK ((((status = 'OPEN'::public.transaction_status) AND (closed_on IS NULL) AND (exit_price IS NULL) AND (result IS NULL)) OR ((status = 'CLOSED'::public.transaction_status) AND (closed_on IS NOT NULL) AND (exit_price IS NOT NULL) AND (result IS NOT NULL))))
);

CREATE VIEW leitura.operacao AS
 SELECT tr.id,
    tr.owner_id,
    t.symbol AS ativo,
    t.currency AS moeda,
    (tr.side)::text AS lado,
    tr.quantity AS quantidade,
    tr.average_cost AS preco_de_entrada,
    tr.exit_price AS preco_de_saida,
    tr.opened_on AS aberta_em,
    tr.closed_on AS fechada_em,
    (tr.status)::text AS status,
    tr.result AS resultado,
    pf.name AS carteira,
    br.name AS corretora,
    (tr.option_contract_id IS NOT NULL) AS e_opcao,
    obj.symbol AS objeto,
    tr.notes AS notas
   FROM (((((public.transactions tr
     LEFT JOIN public.option_contracts oc ON ((oc.id = tr.option_contract_id)))
     LEFT JOIN public.tickers obj ON ((obj.id = oc.underlying_ticker_id)))
     JOIN public.tickers t ON ((t.id = COALESCE(tr.ticker_id, oc.ticker_id))))
     LEFT JOIN public.portfolios pf ON ((pf.id = tr.portfolio_id)))
     LEFT JOIN public.brokers br ON ((br.id = tr.broker_id)));

COMMENT ON VIEW leitura.operacao IS 'Operações de compra e venda de ações e de opções (e_opcao; ativo é o código da opção e objeto, o ativo-objeto). status OPEN ou CLOSED; resultado é o realizado ao fechar, BRUTO (sem custos nem IR). Resultados anteriores a 25/09/2026 podem trazer o fator 0,9996 do modo líquido antigo, sem como distinguir.';

CREATE VIEW leitura.posicao AS
 WITH abertas AS (
         SELECT 'acao'::text AS instrumento,
            p.id AS posicao_id,
            p.owner_id,
            p.portfolio_id,
            p.broker_id,
            t.symbol AS ativo,
            NULL::text AS objeto,
            NULL::numeric AS strike,
            NULL::date AS vencimento,
            NULL::text AS tipo_da_opcao,
            (t.market)::text AS mercado,
            t.currency AS moeda,
            (p.side)::text AS lado,
            p.quantity,
            p.average_cost,
            COALESCE(q.last_price, q.previous_close) AS preco,
            q.observed_at AS cotado_em,
            p.opened_on
           FROM ((public.positions p
             JOIN public.tickers t ON ((t.id = p.ticker_id)))
             LEFT JOIN public.quotes q ON ((q.ticker_id = p.ticker_id)))
        UNION ALL
         SELECT 'opcao',
            op.id,
            op.owner_id,
            op.portfolio_id,
            op.broker_id,
            t.symbol,
            obj.symbol,
            c.strike,
            v.exercise_date,
            (c.option_type)::text AS option_type,
            (t.market)::text AS market,
            t.currency,
            (op.side)::text AS side,
            op.quantity,
            op.average_cost,
            COALESCE(oq.last_price, oq.previous_close) AS "coalesce",
            oq.observed_at,
            op.opened_on
           FROM (((((public.option_positions op
             JOIN public.option_contracts c ON ((c.id = op.contract_id)))
             JOIN public.tickers t ON ((t.id = c.ticker_id)))
             JOIN public.tickers obj ON ((obj.id = c.underlying_ticker_id)))
             JOIN public.option_expirations v ON ((v.id = c.expiration_id)))
             LEFT JOIN public.option_quotes oq ON ((oq.contract_id = op.contract_id)))
        )
 SELECT a.instrumento,
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
    (a.quantity * a.average_cost) AS custo_total,
    a.preco,
    a.cotado_em,
    (((
        CASE
            WHEN (a.lado = 'BUY'::text) THEN 1
            ELSE '-1'::integer
        END)::numeric * a.quantity) * a.preco) AS valor_mercado,
    (((
        CASE
            WHEN (a.lado = 'BUY'::text) THEN 1
            ELSE '-1'::integer
        END)::numeric * (a.preco - a.average_cost)) * a.quantity) AS resultado_aberto,
    a.opened_on AS aberta_em
   FROM ((abertas a
     JOIN public.portfolios pf ON ((pf.id = a.portfolio_id)))
     LEFT JOIN public.brokers br ON ((br.id = a.broker_id)))
  WHERE (a.quantity <> (0)::numeric);

COMMENT ON VIEW leitura.posicao IS 'Posições abertas em ações e opções, com custo, última cotação e resultado não realizado. lado BUY é comprada; SELL, vendida (valor_mercado negativo). Não some BRL com USD.';

CREATE TABLE public.position_ledger_archive (
    id integer NOT NULL,
    occurred_on date NOT NULL,
    ticker_id integer NOT NULL,
    portfolio_id integer NOT NULL,
    broker_id integer NOT NULL,
    instrument character varying(6) NOT NULL,
    source_position_id integer NOT NULL,
    resulting_signed_quantity numeric(24,8) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    owner_id integer NOT NULL,
    CONSTRAINT ck_position_ledger_archive_ck_position_ledger_archive_i_d5f1 CHECK ((instrument IN ('stock', 'option'))),
    CONSTRAINT ck_position_ledger_archive_ck_position_ledger_archive_q_c716 CHECK ((resulting_signed_quantity <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))
);

CREATE VIEW leitura.posicao_historica AS
 SELECT a.id,
    a.owner_id,
    a.occurred_on AS data,
    t.symbol AS ativo,
    pf.name AS carteira,
    br.name AS corretora,
    a.instrument AS instrumento,
    a.resulting_signed_quantity AS quantidade_assinada
   FROM (((public.position_ledger_archive a
     JOIN public.tickers t ON ((t.id = a.ticker_id)))
     LEFT JOIN public.portfolios pf ON ((pf.id = a.portfolio_id)))
     LEFT JOIN public.brokers br ON ((br.id = a.broker_id)));

CREATE TABLE public.dividends (
    id integer NOT NULL,
    broker_id integer NOT NULL,
    ticker_id integer NOT NULL,
    amount numeric(24,8) NOT NULL,
    payment_date date NOT NULL,
    notes character varying(500),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    kind public.income_kind DEFAULT 'DIVIDENDO'::public.income_kind NOT NULL,
    owner_id integer NOT NULL,
    com_date date,
    yield_on_cost numeric(24,8),
    dividend_yield numeric(24,8),
    quotas numeric(24,8),
    invested_total numeric(24,8),
    market_total numeric(24,8),
    average_price numeric(24,8),
    quoted_price numeric(24,8),
    amount_per_share numeric(24,8),
    origin character varying(10) DEFAULT 'planilha'::character varying NOT NULL,
    origin_id character varying(120),
    gross_amount numeric(24,8),
    withheld_amount numeric(24,8),
    superseded_at timestamp with time zone,
    CONSTRAINT ck_dividends_amount_finite CHECK ((amount <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_dividends_amount_per_share_non_negative CHECK (((amount_per_share IS NULL) OR (amount_per_share >= (0)::numeric))),
    CONSTRAINT ck_dividends_amount_positive CHECK ((amount > (0)::numeric)),
    CONSTRAINT ck_dividends_average_price_non_negative CHECK (((average_price IS NULL) OR (average_price >= (0)::numeric))),
    CONSTRAINT ck_dividends_dividend_yield_non_negative CHECK (((dividend_yield IS NULL) OR (dividend_yield >= (0)::numeric))),
    CONSTRAINT ck_dividends_gross_amount_finite CHECK ((gross_amount <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_dividends_gross_amount_positive CHECK (((gross_amount IS NULL) OR (gross_amount > (0)::numeric))),
    CONSTRAINT ck_dividends_invested_total_non_negative CHECK (((invested_total IS NULL) OR (invested_total >= (0)::numeric))),
    CONSTRAINT ck_dividends_market_total_non_negative CHECK (((market_total IS NULL) OR (market_total >= (0)::numeric))),
    CONSTRAINT ck_dividends_origin_id_required_for_cb CHECK ((((origin)::text <> 'cb'::text) OR (origin_id IS NOT NULL))),
    CONSTRAINT ck_dividends_origin_valid CHECK ((origin IN ('planilha', 'cb'))),
    CONSTRAINT ck_dividends_quotas_non_negative CHECK (((quotas IS NULL) OR (quotas >= (0)::numeric))),
    CONSTRAINT ck_dividends_quoted_price_non_negative CHECK (((quoted_price IS NULL) OR (quoted_price >= (0)::numeric))),
    CONSTRAINT ck_dividends_superseded_only_sheet CHECK (((superseded_at IS NULL) OR ((origin)::text = 'planilha'::text))),
    CONSTRAINT ck_dividends_withheld_amount_finite CHECK ((withheld_amount <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_dividends_withheld_amount_non_negative CHECK (((withheld_amount IS NULL) OR (withheld_amount >= (0)::numeric))),
    CONSTRAINT ck_dividends_yield_on_cost_non_negative CHECK (((yield_on_cost IS NULL) OR (yield_on_cost >= (0)::numeric)))
);

CREATE VIEW leitura.provento AS
 SELECT d.id,
    d.owner_id,
    t.symbol AS ativo,
    t.currency AS moeda,
    br.name AS corretora,
    (d.kind)::text AS tipo,
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
   FROM ((public.dividends d
     JOIN public.tickers t ON ((t.id = d.ticker_id)))
     LEFT JOIN public.brokers br ON ((br.id = d.broker_id)))
  WHERE (d.superseded_at IS NULL);

COMMENT ON VIEW leitura.provento IS 'Proventos recebidos. tipo: DIVIDENDO, JCP, ALUGUEL ou CUPOM (cupom de título de renda fixa do exterior; o título fica no CB); valor na moeda do ativo. Desde 01/01/2026 vêm do Controle Bancario e valor é o LÍQUIDO que entrou no caixa (o aluguel já sem IRRF, corretagem e taxas); linhas de planilha que o CB passou a cobrir não aparecem aqui. yield_on_cost e dividend_yield estão em PONTOS PERCENTUAIS (0,42 = 0,42%). Os campos opcionais são nulos quando não informados.';

CREATE TABLE public.app_settings (
    id integer NOT NULL,
    poll_interval_seconds integer NOT NULL,
    risk_free_rate_annual numeric(5,4) NOT NULL,
    benchmark_ticker_id integer,
    stale_alert_seconds integer,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    collector_refresh_requested_at timestamp with time zone,
    collector_agent_seen_at timestamp with time zone,
    collector_agent_status character varying(16) DEFAULT 'waiting'::character varying NOT NULL,
    collector_agent_error character varying(250),
    agent_check_interval_seconds integer DEFAULT 30 NOT NULL,
    collector_schedule_weekdays character varying(13) DEFAULT '0,1,2,3,4'::character varying NOT NULL,
    collector_schedule_start_time time without time zone DEFAULT '09:45:00'::time without time zone NOT NULL,
    collector_schedule_end_time time without time zone DEFAULT '18:10:00'::time without time zone NOT NULL,
    theme character varying(24) DEFAULT 'light'::character varying NOT NULL,
    collector_paused boolean DEFAULT false NOT NULL,
    CONSTRAINT ck_app_settings_agent_check_interval_seconds_range CHECK (((agent_check_interval_seconds >= 5) AND (agent_check_interval_seconds <= 3600))),
    CONSTRAINT ck_app_settings_collector_schedule_time_range CHECK ((collector_schedule_start_time < collector_schedule_end_time)),
    CONSTRAINT ck_app_settings_poll_interval_seconds_range CHECK (((poll_interval_seconds >= 1) AND (poll_interval_seconds <= 3600))),
    CONSTRAINT ck_app_settings_risk_free_rate_annual_finite CHECK ((risk_free_rate_annual <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_app_settings_risk_free_rate_annual_range CHECK (((risk_free_rate_annual >= (0)::numeric) AND (risk_free_rate_annual <= (1)::numeric))),
    CONSTRAINT ck_app_settings_singleton CHECK ((id = 1)),
    CONSTRAINT ck_app_settings_stale_alert_seconds_range CHECK (((stale_alert_seconds IS NULL) OR ((stale_alert_seconds >= 1) AND (stale_alert_seconds <= 86400)))),
    CONSTRAINT ck_app_settings_theme_valid CHECK ((theme IN ('institutional', 'light', 'dark', 'solarized_light', 'solarized_dark', 'dracula', 'nord', 'monokai', 'gray', 'soft_light', 'soft_dark', 'corporate_blue', 'emerald')))
);

CREATE SEQUENCE public.app_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.app_settings_id_seq OWNED BY public.app_settings.id;

CREATE TABLE public.audit_log (
    id integer NOT NULL,
    user_id integer,
    entity character varying(80) NOT NULL,
    entity_id character varying(80),
    action character varying(40) NOT NULL,
    details jsonb,
    ip character varying(45),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.audit_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id;

CREATE SEQUENCE public.brokers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.brokers_id_seq OWNED BY public.brokers.id;

CREATE SEQUENCE public.dividends_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.dividends_id_seq OWNED BY public.dividends.id;

CREATE TABLE public.execution_imports (
    id integer NOT NULL,
    owner_id integer NOT NULL,
    file_name character varying(120),
    period_start date NOT NULL,
    period_end date NOT NULL,
    sha256 character varying(64) NOT NULL,
    row_count integer NOT NULL,
    rows_created integer NOT NULL,
    rows_updated integer NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_execution_imports_counts_non_negative CHECK (((row_count >= 0) AND (rows_created >= 0) AND (rows_updated >= 0))),
    CONSTRAINT ck_execution_imports_period_valid CHECK ((period_end >= period_start)),
    CONSTRAINT ck_execution_imports_sha256_length CHECK ((length((sha256)::text) = 64))
);

CREATE SEQUENCE public.execution_imports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.execution_imports_id_seq OWNED BY public.execution_imports.id;

CREATE TABLE public.executions (
    id integer NOT NULL,
    owner_id integer NOT NULL,
    import_id integer NOT NULL,
    broker_id integer NOT NULL,
    institution character varying(40) NOT NULL,
    trade_date date NOT NULL,
    document character varying(80) NOT NULL,
    document_type character varying(40),
    line_order integer NOT NULL,
    content_hash character varying(64) NOT NULL,
    asset character varying(80) NOT NULL,
    asset_group character varying(20) NOT NULL,
    side character varying(1) NOT NULL,
    cash_operation character varying(1) NOT NULL,
    quantity numeric(24,8) NOT NULL,
    price_without_fees numeric(24,8) NOT NULL,
    price_with_fees numeric(24,8) NOT NULL,
    fees_total numeric(24,8) NOT NULL,
    total_without_fees numeric(24,8) NOT NULL,
    total_with_fees numeric(24,8) NOT NULL,
    day_trade boolean NOT NULL,
    currency character varying(3) NOT NULL,
    in_scope boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    applied_at timestamp with time zone,
    application character varying(20),
    application_note character varying(300),
    CONSTRAINT ck_executions_application_valid CHECK (((application IS NULL) OR (application IN ('aplicada', 'ja_lancada', 'linha_de_base')))),
    CONSTRAINT ck_executions_application_with_date CHECK (((application IS NULL) = (applied_at IS NULL))),
    CONSTRAINT ck_executions_asset_group_valid CHECK ((asset_group IN ('acao', 'opcao', 'etf', 'indice_futuro', 'tesouro_direto', 'acao_eua', 'etf_usa', 'renda_fixa_eua'))),
    CONSTRAINT ck_executions_cash_operation_valid CHECK ((cash_operation IN ('C', 'D'))),
    CONSTRAINT ck_executions_content_hash_length CHECK ((length((content_hash)::text) = 64)),
    CONSTRAINT ck_executions_currency_valid CHECK (((currency)::text ~ '^[A-Z]{3}$'::text)),
    CONSTRAINT ck_executions_fees_non_negative CHECK ((fees_total >= (0)::numeric)),
    CONSTRAINT ck_executions_fees_total_finite CHECK ((fees_total <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_executions_line_order_positive CHECK ((line_order >= 1)),
    CONSTRAINT ck_executions_price_with_fees_finite CHECK ((price_with_fees <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_executions_price_without_fees_finite CHECK ((price_without_fees <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_executions_prices_non_negative CHECK (((price_without_fees >= (0)::numeric) AND (price_with_fees >= (0)::numeric))),
    CONSTRAINT ck_executions_quantity_finite CHECK ((quantity <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_executions_quantity_non_zero CHECK ((quantity <> (0)::numeric)),
    CONSTRAINT ck_executions_side_valid CHECK ((side IN ('C', 'V'))),
    CONSTRAINT ck_executions_total_with_fees_finite CHECK ((total_with_fees <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_executions_total_without_fees_finite CHECK ((total_without_fees <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))
);

CREATE SEQUENCE public.executions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.executions_id_seq OWNED BY public.executions.id;

CREATE TABLE public.myprofit_coletas (
    id integer NOT NULL,
    owner_id integer NOT NULL,
    sha256 character varying(64) NOT NULL,
    ate date NOT NULL,
    summary jsonb NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_myprofit_coletas_sha256_length CHECK ((length((sha256)::text) = 64))
);

CREATE SEQUENCE public.myprofit_coletas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.myprofit_coletas_id_seq OWNED BY public.myprofit_coletas.id;

CREATE TABLE public.myprofit_positions (
    id integer NOT NULL,
    owner_id integer NOT NULL,
    as_of date NOT NULL,
    line_order integer NOT NULL,
    asset character varying(80) NOT NULL,
    group_label character varying(40) NOT NULL,
    in_scope boolean NOT NULL,
    quantity numeric(24,8) NOT NULL,
    average_price numeric(24,8) NOT NULL,
    total_invested numeric(24,8) NOT NULL,
    sha256 character varying(64) NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_myprofit_positions_amounts_non_negative CHECK (((quantity >= (0)::numeric) AND (average_price >= (0)::numeric))),
    CONSTRAINT ck_myprofit_positions_average_price_finite CHECK ((average_price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_myprofit_positions_line_order_positive CHECK ((line_order >= 1)),
    CONSTRAINT ck_myprofit_positions_quantity_finite CHECK ((quantity <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_myprofit_positions_sha256_length CHECK ((length((sha256)::text) = 64)),
    CONSTRAINT ck_myprofit_positions_total_invested_finite CHECK ((total_invested <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))
);

CREATE SEQUENCE public.myprofit_positions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.myprofit_positions_id_seq OWNED BY public.myprofit_positions.id;

CREATE SEQUENCE public.option_contracts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.option_contracts_id_seq OWNED BY public.option_contracts.id;

CREATE SEQUENCE public.option_expirations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.option_expirations_id_seq OWNED BY public.option_expirations.id;

CREATE SEQUENCE public.option_position_movements_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.option_position_movements_id_seq OWNED BY public.option_position_movements.id;

CREATE SEQUENCE public.option_positions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.option_positions_id_seq OWNED BY public.option_positions.id;

CREATE TABLE public.patrimonio_v4_change_counter (
    id integer NOT NULL,
    value integer DEFAULT 0 NOT NULL,
    CONSTRAINT ck_patrimonio_v4_change_counter_ck_patrimonio_v4_change_8518 CHECK ((id = 1)),
    CONSTRAINT ck_patrimonio_v4_change_counter_ck_patrimonio_v4_change_8fc3 CHECK ((value >= 0))
);

CREATE SEQUENCE public.patrimonio_v4_change_counter_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.patrimonio_v4_change_counter_id_seq OWNED BY public.patrimonio_v4_change_counter.id;

CREATE TABLE public.patrimonio_v4_outbox (
    cursor integer NOT NULL,
    owner_id integer,
    resource character varying(32) NOT NULL,
    source_record_id integer NOT NULL,
    operation character varying(8) NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_patrimonio_v4_outbox_ck_patrimonio_v4_outbox_operation_valid CHECK ((operation IN ('upsert', 'delete'))),
    CONSTRAINT ck_patrimonio_v4_outbox_ck_patrimonio_v4_outbox_resource_valid CHECK ((resource IN ('holding', 'income', 'price_current', 'price_history', 'position_ledger', 'activity')))
);

CREATE SEQUENCE public.patrimonio_v4_outbox_cursor_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.patrimonio_v4_outbox_cursor_seq OWNED BY public.patrimonio_v4_outbox.cursor;

CREATE SEQUENCE public.portfolios_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.portfolios_id_seq OWNED BY public.portfolios.id;

CREATE SEQUENCE public.position_ledger_archive_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.position_ledger_archive_id_seq OWNED BY public.position_ledger_archive.id;

CREATE TABLE public.position_movement_archive (
    id integer NOT NULL,
    owner_id integer NOT NULL,
    ticker_id integer NOT NULL,
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
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_0cfb CHECK ((price >= (0)::numeric)),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_1c8e CHECK (((result IS NULL) OR (result <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_271d CHECK ((price <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_29d8 CHECK ((quantity_delta <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_2ac3 CHECK ((resulting_quantity >= (0)::numeric)),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_325f CHECK ((resulting_average_cost >= (0)::numeric)),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_5f8b CHECK ((resulting_average_cost <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_7dc1 CHECK (((source_transaction_id IS NULL) OR (kind IN ('decrease', 'close')))),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_8067 CHECK ((((kind IN ('open', 'increase')) AND (quantity_delta > (0)::numeric)) OR ((kind IN ('decrease', 'close')) AND (quantity_delta < (0)::numeric)) OR ((kind)::text = 'adjustment'::text))),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_877f CHECK ((side IN ('C', 'V'))),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_dcfc CHECK ((kind IN ('open', 'increase', 'decrease', 'adjustment', 'close'))),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_e0a0 CHECK (((kind IN ('decrease', 'close')) = (result IS NOT NULL))),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_f3b1 CHECK ((resulting_quantity <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT ck_position_movement_archive_ck_position_movement_archi_f677 CHECK ((((kind)::text <> 'close'::text) OR ((source_transaction_id IS NOT NULL) AND (source_movement_id IS NULL) AND (resulting_quantity = (0)::numeric))))
);

CREATE SEQUENCE public.position_movement_archive_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.position_movement_archive_id_seq OWNED BY public.position_movement_archive.id;

CREATE SEQUENCE public.position_movements_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.position_movements_id_seq OWNED BY public.position_movements.id;

CREATE TABLE public.position_submissions (
    owner_id integer NOT NULL,
    command character varying(12) NOT NULL,
    token character varying(64) NOT NULL,
    request_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_position_submissions_position_submission_command CHECK ((command IN ('stock', 'option')))
);

CREATE SEQUENCE public.positions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.positions_id_seq OWNED BY public.positions.id;

CREATE SEQUENCE public.quote_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.quote_history_id_seq OWNED BY public.quote_history.id;

CREATE TABLE public.reconciliation_notes (
    id integer NOT NULL,
    owner_id integer NOT NULL,
    kind character varying(10) NOT NULL,
    asset character varying(80) NOT NULL,
    note character varying(300) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_reconciliation_notes_kind_valid CHECK ((kind IN ('posicao', 'venda')))
);

CREATE SEQUENCE public.reconciliation_notes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.reconciliation_notes_id_seq OWNED BY public.reconciliation_notes.id;

CREATE TABLE public.ticker_former_symbols (
    id integer NOT NULL,
    ticker_id integer NOT NULL,
    symbol character varying(24) NOT NULL,
    last_trading_day date NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.ticker_former_symbols_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.ticker_former_symbols_id_seq OWNED BY public.ticker_former_symbols.id;

CREATE SEQUENCE public.tickers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.tickers_id_seq OWNED BY public.tickers.id;

CREATE SEQUENCE public.transactions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.transactions_id_seq OWNED BY public.transactions.id;

CREATE TABLE public.user_preferences (
    user_id integer NOT NULL,
    theme character varying(24) DEFAULT 'institutional'::character varying NOT NULL,
    benchmark_ticker_id integer,
    risk_free_rate_annual numeric(5,4) DEFAULT 0.1075 NOT NULL,
    stale_alert_seconds integer,
    regional_format character varying(2) DEFAULT 'br'::character varying NOT NULL,
    CONSTRAINT ck_user_preferences_regional_format_valid CHECK ((regional_format IN ('br', 'us')))
);

CREATE TABLE public.user_ticker_entitlements (
    user_id integer NOT NULL,
    ticker_id integer NOT NULL,
    first_held_on date NOT NULL
);

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(80) NOT NULL,
    password_hash character varying(255) NOT NULL,
    is_active_user boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    role character varying(20) DEFAULT 'operador'::character varying NOT NULL,
    must_change_password boolean DEFAULT false NOT NULL,
    session_epoch integer DEFAULT 0 NOT NULL,
    CONSTRAINT ck_users_ck_users_role_valid CHECK ((role IN ('admin', 'operador')))
);

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;

ALTER TABLE ONLY public.app_settings ALTER COLUMN id SET DEFAULT nextval('public.app_settings_id_seq'::regclass);

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);

ALTER TABLE ONLY public.brokers ALTER COLUMN id SET DEFAULT nextval('public.brokers_id_seq'::regclass);

ALTER TABLE ONLY public.dividends ALTER COLUMN id SET DEFAULT nextval('public.dividends_id_seq'::regclass);

ALTER TABLE ONLY public.execution_imports ALTER COLUMN id SET DEFAULT nextval('public.execution_imports_id_seq'::regclass);

ALTER TABLE ONLY public.executions ALTER COLUMN id SET DEFAULT nextval('public.executions_id_seq'::regclass);

ALTER TABLE ONLY public.myprofit_coletas ALTER COLUMN id SET DEFAULT nextval('public.myprofit_coletas_id_seq'::regclass);

ALTER TABLE ONLY public.myprofit_positions ALTER COLUMN id SET DEFAULT nextval('public.myprofit_positions_id_seq'::regclass);

ALTER TABLE ONLY public.option_contracts ALTER COLUMN id SET DEFAULT nextval('public.option_contracts_id_seq'::regclass);

ALTER TABLE ONLY public.option_expirations ALTER COLUMN id SET DEFAULT nextval('public.option_expirations_id_seq'::regclass);

ALTER TABLE ONLY public.option_position_movements ALTER COLUMN id SET DEFAULT nextval('public.option_position_movements_id_seq'::regclass);

ALTER TABLE ONLY public.option_positions ALTER COLUMN id SET DEFAULT nextval('public.option_positions_id_seq'::regclass);

ALTER TABLE ONLY public.patrimonio_v4_change_counter ALTER COLUMN id SET DEFAULT nextval('public.patrimonio_v4_change_counter_id_seq'::regclass);

ALTER TABLE ONLY public.patrimonio_v4_outbox ALTER COLUMN cursor SET DEFAULT nextval('public.patrimonio_v4_outbox_cursor_seq'::regclass);

ALTER TABLE ONLY public.portfolios ALTER COLUMN id SET DEFAULT nextval('public.portfolios_id_seq'::regclass);

ALTER TABLE ONLY public.position_ledger_archive ALTER COLUMN id SET DEFAULT nextval('public.position_ledger_archive_id_seq'::regclass);

ALTER TABLE ONLY public.position_movement_archive ALTER COLUMN id SET DEFAULT nextval('public.position_movement_archive_id_seq'::regclass);

ALTER TABLE ONLY public.position_movements ALTER COLUMN id SET DEFAULT nextval('public.position_movements_id_seq'::regclass);

ALTER TABLE ONLY public.positions ALTER COLUMN id SET DEFAULT nextval('public.positions_id_seq'::regclass);

ALTER TABLE ONLY public.quote_history ALTER COLUMN id SET DEFAULT nextval('public.quote_history_id_seq'::regclass);

ALTER TABLE ONLY public.reconciliation_notes ALTER COLUMN id SET DEFAULT nextval('public.reconciliation_notes_id_seq'::regclass);

ALTER TABLE ONLY public.ticker_former_symbols ALTER COLUMN id SET DEFAULT nextval('public.ticker_former_symbols_id_seq'::regclass);

ALTER TABLE ONLY public.tickers ALTER COLUMN id SET DEFAULT nextval('public.tickers_id_seq'::regclass);

ALTER TABLE ONLY public.transactions ALTER COLUMN id SET DEFAULT nextval('public.transactions_id_seq'::regclass);

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT pk_app_settings PRIMARY KEY (id);

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT pk_audit_log PRIMARY KEY (id);

ALTER TABLE ONLY public.brokers
    ADD CONSTRAINT pk_brokers PRIMARY KEY (id);

ALTER TABLE ONLY public.dividends
    ADD CONSTRAINT pk_dividends PRIMARY KEY (id);

ALTER TABLE ONLY public.execution_imports
    ADD CONSTRAINT pk_execution_imports PRIMARY KEY (id);

ALTER TABLE ONLY public.executions
    ADD CONSTRAINT pk_executions PRIMARY KEY (id);

ALTER TABLE ONLY public.myprofit_coletas
    ADD CONSTRAINT pk_myprofit_coletas PRIMARY KEY (id);

ALTER TABLE ONLY public.myprofit_positions
    ADD CONSTRAINT pk_myprofit_positions PRIMARY KEY (id);

ALTER TABLE ONLY public.option_contracts
    ADD CONSTRAINT pk_option_contracts PRIMARY KEY (id);

ALTER TABLE ONLY public.option_expirations
    ADD CONSTRAINT pk_option_expirations PRIMARY KEY (id);

ALTER TABLE ONLY public.option_position_movements
    ADD CONSTRAINT pk_option_position_movements PRIMARY KEY (id);

ALTER TABLE ONLY public.option_positions
    ADD CONSTRAINT pk_option_positions PRIMARY KEY (id);

ALTER TABLE ONLY public.option_quotes
    ADD CONSTRAINT pk_option_quotes_new PRIMARY KEY (contract_id);

ALTER TABLE ONLY public.patrimonio_v4_change_counter
    ADD CONSTRAINT pk_patrimonio_v4_change_counter PRIMARY KEY (id);

ALTER TABLE ONLY public.patrimonio_v4_outbox
    ADD CONSTRAINT pk_patrimonio_v4_outbox PRIMARY KEY (cursor);

ALTER TABLE ONLY public.portfolio_tickers
    ADD CONSTRAINT pk_portfolio_tickers PRIMARY KEY (portfolio_id, ticker_id);

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT pk_portfolios PRIMARY KEY (id);

ALTER TABLE ONLY public.position_ledger_archive
    ADD CONSTRAINT pk_position_ledger_archive PRIMARY KEY (id);

ALTER TABLE ONLY public.position_movement_archive
    ADD CONSTRAINT pk_position_movement_archive PRIMARY KEY (id);

ALTER TABLE ONLY public.position_movements
    ADD CONSTRAINT pk_position_movements PRIMARY KEY (id);

ALTER TABLE ONLY public.position_submissions
    ADD CONSTRAINT pk_position_submissions PRIMARY KEY (owner_id, command, token);

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT pk_positions PRIMARY KEY (id);

ALTER TABLE ONLY public.quote_history
    ADD CONSTRAINT pk_quote_history PRIMARY KEY (id);

ALTER TABLE ONLY public.quotes
    ADD CONSTRAINT pk_quotes_new PRIMARY KEY (ticker_id);

ALTER TABLE ONLY public.reconciliation_notes
    ADD CONSTRAINT pk_reconciliation_notes PRIMARY KEY (id);

ALTER TABLE ONLY public.ticker_former_symbols
    ADD CONSTRAINT pk_ticker_former_symbols PRIMARY KEY (id);

ALTER TABLE ONLY public.tickers
    ADD CONSTRAINT pk_tickers PRIMARY KEY (id);

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT pk_transactions PRIMARY KEY (id);

ALTER TABLE ONLY public.user_preferences
    ADD CONSTRAINT pk_user_preferences PRIMARY KEY (user_id);

ALTER TABLE ONLY public.user_ticker_entitlements
    ADD CONSTRAINT pk_user_ticker_entitlements PRIMARY KEY (user_id, ticker_id);

ALTER TABLE ONLY public.users
    ADD CONSTRAINT pk_users PRIMARY KEY (id);

ALTER TABLE ONLY public.brokers
    ADD CONSTRAINT uq_brokers_acronym UNIQUE (acronym);

ALTER TABLE ONLY public.brokers
    ADD CONSTRAINT uq_brokers_name UNIQUE (name);

ALTER TABLE ONLY public.execution_imports
    ADD CONSTRAINT uq_execution_imports_id_owner UNIQUE (id, owner_id);

ALTER TABLE ONLY public.executions
    ADD CONSTRAINT uq_executions_owner_document_line UNIQUE (owner_id, document, line_order);

ALTER TABLE ONLY public.myprofit_coletas
    ADD CONSTRAINT uq_myprofit_coletas_owner_sha256 UNIQUE (owner_id, sha256);

ALTER TABLE ONLY public.myprofit_positions
    ADD CONSTRAINT uq_myprofit_positions_owner_date_line UNIQUE (owner_id, as_of, line_order);

ALTER TABLE ONLY public.option_contracts
    ADD CONSTRAINT uq_option_contracts_ticker_id UNIQUE (ticker_id);

ALTER TABLE ONLY public.option_expirations
    ADD CONSTRAINT uq_option_expirations_call_code UNIQUE (call_code);

ALTER TABLE ONLY public.option_expirations
    ADD CONSTRAINT uq_option_expirations_exercise_date UNIQUE (exercise_date);

ALTER TABLE ONLY public.option_expirations
    ADD CONSTRAINT uq_option_expirations_put_code UNIQUE (put_code);

ALTER TABLE ONLY public.option_position_movements
    ADD CONSTRAINT uq_option_position_movements_transaction_id UNIQUE (transaction_id);

ALTER TABLE ONLY public.option_positions
    ADD CONSTRAINT uq_option_positions_chave UNIQUE (owner_id, portfolio_id, broker_id, contract_id, side);

ALTER TABLE ONLY public.option_positions
    ADD CONSTRAINT uq_option_positions_id_owner UNIQUE (id, owner_id);

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT uq_portfolios_id_owner UNIQUE (id, owner_id);

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT uq_portfolios_owner_name UNIQUE (owner_id, name);

ALTER TABLE ONLY public.position_movement_archive
    ADD CONSTRAINT uq_position_movement_archive_source UNIQUE (owner_id, source_position_id, source_movement_id);

ALTER TABLE ONLY public.position_movements
    ADD CONSTRAINT uq_position_movements_transaction_id UNIQUE (transaction_id);

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT uq_positions_chave UNIQUE (owner_id, portfolio_id, broker_id, ticker_id, side);

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT uq_positions_id_owner UNIQUE (id, owner_id);

ALTER TABLE ONLY public.quote_history
    ADD CONSTRAINT uq_quote_history_ticker_date UNIQUE (ticker_id, recorded_date);

ALTER TABLE ONLY public.reconciliation_notes
    ADD CONSTRAINT uq_reconciliation_notes_owner_kind_asset UNIQUE (owner_id, kind, asset);

ALTER TABLE ONLY public.ticker_former_symbols
    ADD CONSTRAINT uq_ticker_former_symbols_symbol UNIQUE (symbol);

ALTER TABLE ONLY public.tickers
    ADD CONSTRAINT uq_tickers_symbol UNIQUE (symbol);

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT uq_transactions_id_owner UNIQUE (id, owner_id);

ALTER TABLE ONLY public.users
    ADD CONSTRAINT uq_users_username UNIQUE (username);

CREATE INDEX ix_audit_log_created_at ON public.audit_log USING btree (created_at);

CREATE INDEX ix_audit_log_entity ON public.audit_log USING btree (entity, entity_id);

CREATE INDEX ix_audit_log_user_id ON public.audit_log USING btree (user_id);

CREATE INDEX ix_dividends_broker_id ON public.dividends USING btree (broker_id);

CREATE INDEX ix_dividends_owner_id ON public.dividends USING btree (owner_id);

CREATE INDEX ix_dividends_payment_date ON public.dividends USING btree (payment_date);

CREATE INDEX ix_dividends_superseded_at ON public.dividends USING btree (superseded_at);

CREATE INDEX ix_dividends_ticker_id ON public.dividends USING btree (ticker_id);

CREATE INDEX ix_execution_imports_owner_id ON public.execution_imports USING btree (owner_id);

CREATE INDEX ix_executions_import_id ON public.executions USING btree (import_id);

CREATE INDEX ix_executions_owner_broker_date ON public.executions USING btree (owner_id, broker_id, trade_date);

CREATE INDEX ix_executions_owner_id ON public.executions USING btree (owner_id);

CREATE INDEX ix_executions_pendentes ON public.executions USING btree (owner_id, trade_date) WHERE (in_scope AND (applied_at IS NULL));

CREATE INDEX ix_myprofit_coletas_owner_ate ON public.myprofit_coletas USING btree (owner_id, ate);

CREATE INDEX ix_myprofit_positions_owner_as_of ON public.myprofit_positions USING btree (owner_id, as_of);

CREATE INDEX ix_myprofit_positions_owner_id ON public.myprofit_positions USING btree (owner_id);

CREATE INDEX ix_option_position_movements_option_position_id ON public.option_position_movements USING btree (option_position_id);

CREATE INDEX ix_option_position_movements_owner_id ON public.option_position_movements USING btree (owner_id);

CREATE INDEX ix_option_positions_broker_id ON public.option_positions USING btree (broker_id);

CREATE INDEX ix_option_positions_contract_id ON public.option_positions USING btree (contract_id);

CREATE INDEX ix_option_positions_owner_id ON public.option_positions USING btree (owner_id);

CREATE INDEX ix_option_positions_portfolio_id ON public.option_positions USING btree (portfolio_id);

CREATE INDEX ix_option_quotes_observed_at ON public.option_quotes USING btree (observed_at);

CREATE INDEX ix_patrimonio_v4_outbox_owner_cursor ON public.patrimonio_v4_outbox USING btree (owner_id, cursor);

CREATE INDEX ix_patrimonio_v4_outbox_owner_id ON public.patrimonio_v4_outbox USING btree (owner_id);

CREATE INDEX ix_portfolios_owner_id ON public.portfolios USING btree (owner_id);

CREATE INDEX ix_position_ledger_archive_broker_id ON public.position_ledger_archive USING btree (broker_id);

CREATE INDEX ix_position_ledger_archive_occurred_on ON public.position_ledger_archive USING btree (occurred_on);

CREATE INDEX ix_position_ledger_archive_owner_id ON public.position_ledger_archive USING btree (owner_id);

CREATE INDEX ix_position_ledger_archive_portfolio_id ON public.position_ledger_archive USING btree (portfolio_id);

CREATE INDEX ix_position_ledger_archive_ticker_id ON public.position_ledger_archive USING btree (ticker_id);

CREATE INDEX ix_position_movement_archive_broker_id ON public.position_movement_archive USING btree (broker_id);

CREATE INDEX ix_position_movement_archive_occurred_on ON public.position_movement_archive USING btree (occurred_on);

CREATE INDEX ix_position_movement_archive_owner_id ON public.position_movement_archive USING btree (owner_id);

CREATE INDEX ix_position_movement_archive_portfolio_id ON public.position_movement_archive USING btree (portfolio_id);

CREATE INDEX ix_position_movement_archive_ticker_id ON public.position_movement_archive USING btree (ticker_id);

CREATE INDEX ix_position_movements_owner_id ON public.position_movements USING btree (owner_id);

CREATE INDEX ix_position_movements_position_id ON public.position_movements USING btree (position_id);

CREATE INDEX ix_positions_broker_id ON public.positions USING btree (broker_id);

CREATE INDEX ix_positions_owner_id ON public.positions USING btree (owner_id);

CREATE INDEX ix_positions_portfolio_id ON public.positions USING btree (portfolio_id);

CREATE INDEX ix_positions_ticker_id ON public.positions USING btree (ticker_id);

CREATE INDEX ix_quote_history_recorded_date ON public.quote_history USING btree (recorded_date);

CREATE INDEX ix_quote_history_ticker_id ON public.quote_history USING btree (ticker_id);

CREATE INDEX ix_quotes_observed_at ON public.quotes USING btree (observed_at);

CREATE INDEX ix_reconciliation_notes_owner_id ON public.reconciliation_notes USING btree (owner_id);

CREATE INDEX ix_ticker_former_symbols_ticker_id ON public.ticker_former_symbols USING btree (ticker_id);

CREATE INDEX ix_transactions_broker_id ON public.transactions USING btree (broker_id);

CREATE INDEX ix_transactions_closed_on ON public.transactions USING btree (closed_on);

CREATE INDEX ix_transactions_option_contract_id ON public.transactions USING btree (option_contract_id);

CREATE INDEX ix_transactions_owner_id ON public.transactions USING btree (owner_id);

CREATE INDEX ix_transactions_portfolio_id ON public.transactions USING btree (portfolio_id);

CREATE INDEX ix_transactions_ticker_id ON public.transactions USING btree (ticker_id);

CREATE UNIQUE INDEX uq_dividends_import_key_planilha ON public.dividends USING btree (owner_id, ticker_id, broker_id, kind, payment_date, amount) WHERE ((origin)::text = 'planilha'::text);

CREATE UNIQUE INDEX uq_dividends_origem_cb ON public.dividends USING btree (owner_id, origin_id) WHERE ((origin)::text = 'cb'::text);

CREATE UNIQUE INDEX uq_transactions_open_source_position_option ON public.transactions USING btree (source_position_id) WHERE ((status = 'OPEN'::public.transaction_status) AND (option_contract_id IS NOT NULL));

CREATE UNIQUE INDEX uq_transactions_open_source_position_stock ON public.transactions USING btree (source_position_id) WHERE ((status = 'OPEN'::public.transaction_status) AND (ticker_id IS NOT NULL));

CREATE TRIGGER patrimonio_v4_outbox_dividends AFTER INSERT OR DELETE OR UPDATE ON public.dividends FOR EACH ROW EXECUTE FUNCTION public.patrimonio_v4_outbox_emit('income', 'id', 'owner_id');

CREATE TRIGGER patrimonio_v4_outbox_position_movement_archive AFTER INSERT OR DELETE OR UPDATE ON public.position_movement_archive FOR EACH ROW EXECUTE FUNCTION public.patrimonio_v4_outbox_emit('position_ledger', 'id', 'owner_id');

CREATE TRIGGER patrimonio_v4_outbox_positions AFTER INSERT OR DELETE OR UPDATE ON public.positions FOR EACH ROW EXECUTE FUNCTION public.patrimonio_v4_outbox_emit('holding', 'id', 'owner_id');

CREATE TRIGGER patrimonio_v4_outbox_quote_history AFTER INSERT OR DELETE OR UPDATE ON public.quote_history FOR EACH ROW EXECUTE FUNCTION public.patrimonio_v4_outbox_emit('price_history', 'id', '');

CREATE TRIGGER patrimonio_v4_outbox_quotes AFTER INSERT OR DELETE OR UPDATE ON public.quotes FOR EACH ROW EXECUTE FUNCTION public.patrimonio_v4_outbox_emit('price_current', 'ticker_id', '');

CREATE TRIGGER patrimonio_v4_outbox_transactions AFTER INSERT OR DELETE OR UPDATE ON public.transactions FOR EACH ROW EXECUTE FUNCTION public.patrimonio_v4_outbox_emit('activity', 'id', 'owner_id');

CREATE TRIGGER versao_edicao BEFORE UPDATE ON public.option_positions FOR EACH ROW EXECUTE FUNCTION public.incrementar_versao_posicao();

CREATE TRIGGER versao_edicao BEFORE UPDATE ON public.positions FOR EACH ROW EXECUTE FUNCTION public.incrementar_versao_posicao();

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT fk_app_settings_benchmark_ticker_id_tickers FOREIGN KEY (benchmark_ticker_id) REFERENCES public.tickers(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT fk_audit_log_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.dividends
    ADD CONSTRAINT fk_dividends_broker_id_brokers FOREIGN KEY (broker_id) REFERENCES public.brokers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.dividends
    ADD CONSTRAINT fk_dividends_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.dividends
    ADD CONSTRAINT fk_dividends_ticker_id_tickers FOREIGN KEY (ticker_id) REFERENCES public.tickers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.execution_imports
    ADD CONSTRAINT fk_execution_imports_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.executions
    ADD CONSTRAINT fk_executions_broker_id_brokers FOREIGN KEY (broker_id) REFERENCES public.brokers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.executions
    ADD CONSTRAINT fk_executions_import_owner FOREIGN KEY (import_id, owner_id) REFERENCES public.execution_imports(id, owner_id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.executions
    ADD CONSTRAINT fk_executions_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.myprofit_coletas
    ADD CONSTRAINT fk_myprofit_coletas_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.myprofit_positions
    ADD CONSTRAINT fk_myprofit_positions_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.option_contracts
    ADD CONSTRAINT fk_option_contracts_expiration_id_option_expirations FOREIGN KEY (expiration_id) REFERENCES public.option_expirations(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.option_contracts
    ADD CONSTRAINT fk_option_contracts_ticker_id_tickers FOREIGN KEY (ticker_id) REFERENCES public.tickers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.option_contracts
    ADD CONSTRAINT fk_option_contracts_underlying_ticker_id_tickers FOREIGN KEY (underlying_ticker_id) REFERENCES public.tickers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.option_position_movements
    ADD CONSTRAINT fk_option_position_movements_option_position_id_option__437c FOREIGN KEY (option_position_id) REFERENCES public.option_positions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.option_position_movements
    ADD CONSTRAINT fk_option_position_movements_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.option_position_movements
    ADD CONSTRAINT fk_option_position_movements_position_owner FOREIGN KEY (option_position_id, owner_id) REFERENCES public.option_positions(id, owner_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.option_position_movements
    ADD CONSTRAINT fk_option_position_movements_transaction_id_transactions FOREIGN KEY (transaction_id) REFERENCES public.transactions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.option_position_movements
    ADD CONSTRAINT fk_option_position_movements_transaction_owner FOREIGN KEY (transaction_id, owner_id) REFERENCES public.transactions(id, owner_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.option_positions
    ADD CONSTRAINT fk_option_positions_broker_id_brokers FOREIGN KEY (broker_id) REFERENCES public.brokers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.option_positions
    ADD CONSTRAINT fk_option_positions_contract_id_option_contracts FOREIGN KEY (contract_id) REFERENCES public.option_contracts(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.option_positions
    ADD CONSTRAINT fk_option_positions_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.option_positions
    ADD CONSTRAINT fk_option_positions_portfolio_id_portfolios FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.option_positions
    ADD CONSTRAINT fk_option_positions_portfolio_owner FOREIGN KEY (portfolio_id, owner_id) REFERENCES public.portfolios(id, owner_id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.option_quotes
    ADD CONSTRAINT fk_option_quotes_new_contract_id_option_contracts FOREIGN KEY (contract_id) REFERENCES public.option_contracts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.portfolio_tickers
    ADD CONSTRAINT fk_portfolio_tickers_portfolio_id_portfolios FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.portfolio_tickers
    ADD CONSTRAINT fk_portfolio_tickers_ticker_id_tickers FOREIGN KEY (ticker_id) REFERENCES public.tickers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT fk_portfolios_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_ledger_archive
    ADD CONSTRAINT fk_position_ledger_archive_broker_id_brokers FOREIGN KEY (broker_id) REFERENCES public.brokers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_ledger_archive
    ADD CONSTRAINT fk_position_ledger_archive_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_ledger_archive
    ADD CONSTRAINT fk_position_ledger_archive_portfolio_id_portfolios FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_ledger_archive
    ADD CONSTRAINT fk_position_ledger_archive_portfolio_owner FOREIGN KEY (portfolio_id, owner_id) REFERENCES public.portfolios(id, owner_id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_ledger_archive
    ADD CONSTRAINT fk_position_ledger_archive_ticker_id_tickers FOREIGN KEY (ticker_id) REFERENCES public.tickers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_movement_archive
    ADD CONSTRAINT fk_position_movement_archive_broker_id_brokers FOREIGN KEY (broker_id) REFERENCES public.brokers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_movement_archive
    ADD CONSTRAINT fk_position_movement_archive_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_movement_archive
    ADD CONSTRAINT fk_position_movement_archive_portfolio_id_portfolios FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_movement_archive
    ADD CONSTRAINT fk_position_movement_archive_portfolio_owner FOREIGN KEY (portfolio_id, owner_id) REFERENCES public.portfolios(id, owner_id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_movement_archive
    ADD CONSTRAINT fk_position_movement_archive_ticker_id_tickers FOREIGN KEY (ticker_id) REFERENCES public.tickers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_movements
    ADD CONSTRAINT fk_position_movements_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.position_movements
    ADD CONSTRAINT fk_position_movements_position_id_positions FOREIGN KEY (position_id) REFERENCES public.positions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.position_movements
    ADD CONSTRAINT fk_position_movements_position_owner FOREIGN KEY (position_id, owner_id) REFERENCES public.positions(id, owner_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.position_movements
    ADD CONSTRAINT fk_position_movements_transaction_id_transactions FOREIGN KEY (transaction_id) REFERENCES public.transactions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.position_movements
    ADD CONSTRAINT fk_position_movements_transaction_owner FOREIGN KEY (transaction_id, owner_id) REFERENCES public.transactions(id, owner_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.position_submissions
    ADD CONSTRAINT fk_position_submissions_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT fk_positions_broker_id_brokers FOREIGN KEY (broker_id) REFERENCES public.brokers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT fk_positions_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT fk_positions_portfolio_id_portfolios FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT fk_positions_portfolio_owner FOREIGN KEY (portfolio_id, owner_id) REFERENCES public.portfolios(id, owner_id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT fk_positions_ticker_id_tickers FOREIGN KEY (ticker_id) REFERENCES public.tickers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.quote_history
    ADD CONSTRAINT fk_quote_history_ticker_id_tickers FOREIGN KEY (ticker_id) REFERENCES public.tickers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.quotes
    ADD CONSTRAINT fk_quotes_new_ticker_id_tickers FOREIGN KEY (ticker_id) REFERENCES public.tickers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.reconciliation_notes
    ADD CONSTRAINT fk_reconciliation_notes_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.ticker_former_symbols
    ADD CONSTRAINT fk_ticker_former_symbols_ticker_id_tickers FOREIGN KEY (ticker_id) REFERENCES public.tickers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT fk_transactions_broker_id_brokers FOREIGN KEY (broker_id) REFERENCES public.brokers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT fk_transactions_option_contract_id_option_contracts FOREIGN KEY (option_contract_id) REFERENCES public.option_contracts(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT fk_transactions_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT fk_transactions_portfolio_id_portfolios FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT fk_transactions_portfolio_owner FOREIGN KEY (portfolio_id, owner_id) REFERENCES public.portfolios(id, owner_id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT fk_transactions_ticker_id_tickers FOREIGN KEY (ticker_id) REFERENCES public.tickers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.user_preferences
    ADD CONSTRAINT fk_user_preferences_benchmark_ticker_id_tickers FOREIGN KEY (benchmark_ticker_id) REFERENCES public.tickers(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.user_preferences
    ADD CONSTRAINT fk_user_preferences_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.user_ticker_entitlements
    ADD CONSTRAINT fk_user_ticker_entitlements_ticker_id_tickers FOREIGN KEY (ticker_id) REFERENCES public.tickers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.user_ticker_entitlements
    ADD CONSTRAINT fk_user_ticker_entitlements_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

INSERT INTO public.app_settings (id, poll_interval_seconds, risk_free_rate_annual, benchmark_ticker_id, stale_alert_seconds, updated_at, collector_refresh_requested_at, collector_agent_seen_at, collector_agent_status, collector_agent_error, agent_check_interval_seconds, collector_schedule_weekdays, collector_schedule_start_time, collector_schedule_end_time, theme, collector_paused) VALUES (1, 2, 0.1075, NULL, NULL, CURRENT_TIMESTAMP, NULL, NULL, 'waiting', NULL, 30, '0,1,2,3,4', '09:45:00', '18:10:00', 'light', false);

INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (1, '2024F', '2024R', '2024-06-21');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (2, '2024G', '2024S', '2024-07-19');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (3, '2024H', '2024T', '2024-08-16');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (4, '2024I', '2024U', '2024-09-20');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (5, '2024J', '2024V', '2024-10-18');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (6, '2024K', '2024W', '2024-11-14');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (7, '2024L', '2024X', '2024-12-20');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (8, '2025A', '2025M', '2025-01-17');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (9, '2025B', '2025N', '2025-02-21');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (10, '2025C', '2025O', '2025-03-21');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (11, '2025D', '2025P', '2025-04-18');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (12, '2025E', '2025Q', '2025-05-16');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (13, '2025F', '2025R', '2025-06-20');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (14, '2025G', '2025S', '2025-07-18');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (15, '2025H', '2025T', '2025-08-22');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (16, '2025I', '2025U', '2025-09-19');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (17, '2025J', '2025V', '2025-10-17');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (18, '2025K', '2025W', '2025-11-21');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (19, '2025L', '2025X', '2025-12-19');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (20, '2026A', '2026M', '2026-01-16');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (21, '2026B', '2026N', '2026-02-20');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (22, '2026C', '2026O', '2026-03-20');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (23, '2026D', '2026P', '2026-04-17');
INSERT INTO public.option_expirations (id, call_code, put_code, exercise_date) VALUES (24, '2026E', '2026Q', '2026-05-15');

INSERT INTO public.patrimonio_v4_change_counter (id, value) VALUES (1, 0);

SELECT pg_catalog.setval('public.option_expirations_id_seq', 24, true);

SELECT pg_catalog.setval('public.portfolios_id_seq', 3, true);
