"""Isola fatos financeiros por usuário e torna snapshots de preço globais.

Legado só pode ser adotado pelo login explícito ``mspa``. A revisão falha
antes de modificar registros quando encontra fatos e esse usuário não existe.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260912_0016"
down_revision = "20260904_0015"
branch_labels = None
depends_on = None

_OWNED = (
    "portfolios", "positions", "option_positions", "transactions", "dividends",
    "position_movements", "option_position_movements", "position_ledger_archive",
)
_FACT_TABLES = tuple(table for table in _OWNED if table != "portfolios")


def _legacy_exists(bind: sa.Connection) -> bool:
    return any(
        bind.execute(sa.text(f"select exists(select 1 from {table})")).scalar()
        for table in _FACT_TABLES
    )


def _only_baseline_portfolios(bind: sa.Connection) -> bool:
    """Reconhece os três recipientes que a revisão inicial semeava.

    Um banco recém-criado ainda não tem usuários, mas a revisão 0001 criou
    BRL, USD e Simulada. Eles não são fatos financeiros e não podem receber
    um dono inventado. Qualquer carteira adicional ou associação a ticker é
    dado configurado real e requer o mspa explicitamente.
    """
    portfolios = bind.execute(sa.text(
        "select name,currency,simulated,is_active,description from portfolios order by name"
    )).all()
    links = bind.execute(sa.text("select count(*) from portfolio_tickers")).scalar_one()
    # Um banco sem recipientes também é vazio. Recipientes modificados são
    # configuração do usuário, mesmo que conservem os nomes do bootstrap.
    defaults = [("BRL", "BRL", False, True, None), ("Simulada", None, True, True, None),
                ("USD", "USD", False, True, None)]
    return (not portfolios or portfolios == defaults) and links == 0


def upgrade() -> None:
    bind = op.get_bind()
    legacy = _legacy_exists(bind)
    mspa_id = bind.execute(sa.text("select id from users where username = 'mspa'")).scalar()
    if legacy and mspa_id is None:
        raise RuntimeError("Há registros financeiros legados, mas o login obrigatório 'mspa' não existe.")
    if mspa_id is None and not legacy:
        if not _only_baseline_portfolios(bind):
            raise RuntimeError("Há carteiras legadas, mas o login obrigatório 'mspa' não existe.")
        bind.execute(sa.text("delete from portfolios"))

    for table in _OWNED:
        op.add_column(table, sa.Column("owner_id", sa.Integer(), nullable=True))
        op.create_index(op.f(f"ix_{table}_owner_id"), table, ["owner_id"])
        op.create_foreign_key(op.f(f"fk_{table}_owner_id_users"), table, "users", ["owner_id"], ["id"], ondelete="RESTRICT")

    if mspa_id is not None:
        for table in ("portfolios", "positions", "option_positions", "transactions", "dividends", "position_ledger_archive"):
            bind.execute(sa.text(f"update {table} set owner_id = :owner where owner_id is null"), {"owner": mspa_id})
        bind.execute(sa.text("update position_movements m set owner_id=p.owner_id from positions p where m.position_id=p.id"))
        bind.execute(sa.text("update option_position_movements m set owner_id=p.owner_id from option_positions p where m.option_position_id=p.id"))

    for table in _OWNED:
        missing = bind.execute(sa.text(f"select count(*) from {table} where owner_id is null")).scalar_one()
        if missing:
            raise RuntimeError(f"Migração de propriedade incompleta em {table}: {missing} registro(s) sem dono.")
        op.alter_column(table, "owner_id", nullable=False)

    op.drop_constraint(op.f("uq_portfolios_name"), "portfolios", type_="unique")
    op.create_unique_constraint("uq_portfolios_owner_name", "portfolios", ["owner_id", "name"])

    # As FKs simples preservam as referências históricas; estas chaves
    # compostas impedem que uma escrita futura misture registros financeiros
    # de donos distintos, inclusive fora das rotas Flask.
    op.create_unique_constraint("uq_portfolios_id_owner", "portfolios", ["id", "owner_id"])
    op.create_unique_constraint("uq_positions_id_owner", "positions", ["id", "owner_id"])
    op.create_unique_constraint("uq_option_positions_id_owner", "option_positions", ["id", "owner_id"])
    op.create_unique_constraint("uq_transactions_id_owner", "transactions", ["id", "owner_id"])
    op.create_foreign_key(
        "fk_positions_portfolio_owner", "positions", "portfolios",
        ["portfolio_id", "owner_id"], ["id", "owner_id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_option_positions_portfolio_owner", "option_positions", "portfolios",
        ["portfolio_id", "owner_id"], ["id", "owner_id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_transactions_portfolio_owner", "transactions", "portfolios",
        ["portfolio_id", "owner_id"], ["id", "owner_id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_position_movements_position_owner", "position_movements", "positions",
        ["position_id", "owner_id"], ["id", "owner_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_position_movements_transaction_owner", "position_movements", "transactions",
        ["transaction_id", "owner_id"], ["id", "owner_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_option_position_movements_position_owner", "option_position_movements", "option_positions",
        ["option_position_id", "owner_id"], ["id", "owner_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_option_position_movements_transaction_owner", "option_position_movements", "transactions",
        ["transaction_id", "owner_id"], ["id", "owner_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_position_ledger_archive_portfolio_owner", "position_ledger_archive", "portfolios",
        ["portfolio_id", "owner_id"], ["id", "owner_id"], ondelete="RESTRICT",
    )

    op.create_table(
        "user_ticker_entitlements",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("first_held_on", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id", "ticker_id"),
    )
    bind.execute(sa.text("""
        insert into user_ticker_entitlements(user_id,ticker_id,first_held_on)
        select owner_id,ticker_id,min(held_on) from (
          select owner_id,ticker_id,opened_on held_on from positions
          union all select p.owner_id,c.ticker_id,p.opened_on from option_positions p join option_contracts c on c.id=p.contract_id
          union all select owner_id,ticker_id,opened_on from transactions where ticker_id is not null
          union all select t.owner_id,c.ticker_id,t.opened_on from transactions t join option_contracts c on c.id=t.option_contract_id
          union all select owner_id,ticker_id,payment_date from dividends
          union all select owner_id,ticker_id,occurred_on from position_ledger_archive
        ) facts group by owner_id,ticker_id
    """))

    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("theme", sa.String(24), nullable=False, server_default=sa.text("'institutional'")),
        sa.Column("benchmark_ticker_id", sa.Integer(), nullable=True),
        sa.Column("risk_free_rate_annual", sa.Numeric(5, 4), nullable=False, server_default=sa.text("0.1075")),
        sa.Column("stale_alert_seconds", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["benchmark_ticker_id"], ["tickers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    # Preferências que antes viviam no singleton passam a existir para cada
    # conta. A configuração histórica pertence ao mspa, o único dono dos
    # fatos legados; os demais começam nos padrões seguros do schema.
    bind.execute(sa.text("""
        insert into user_preferences(user_id)
        select id from users
    """))
    if mspa_id is not None:
        bind.execute(sa.text("""
            update user_preferences preference
            set theme=settings.theme,
                benchmark_ticker_id=case
                    when exists (
                        select 1 from user_ticker_entitlements entitlement
                        where entitlement.user_id=:owner
                          and entitlement.ticker_id=settings.benchmark_ticker_id
                    ) then settings.benchmark_ticker_id
                    else null
                end,
                risk_free_rate_annual=settings.risk_free_rate_annual,
                stale_alert_seconds=settings.stale_alert_seconds
            from app_settings settings
            where preference.user_id = :owner and settings.id = 1
        """), {"owner": mspa_id})

    # As tabelas antigas eram por posição. Reconstroi um snapshot por
    # instrumento, escolhendo de modo determinístico o mais recente.
    op.create_table(
        "quotes_new",
        sa.Column("ticker_id", sa.Integer(), nullable=False), sa.Column("last_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("buy_price", sa.Numeric(24, 8)), sa.Column("sell_price", sa.Numeric(24, 8)),
        sa.Column("buy_observed_at", sa.DateTime(timezone=True)), sa.Column("sell_observed_at", sa.DateTime(timezone=True)),
        sa.Column("previous_close", sa.Numeric(24, 8), nullable=False), sa.Column("instrument_status", sa.String(16), nullable=False),
        sa.Column("source_status", sa.String(16), nullable=False), sa.Column("error_message", sa.String(250)), sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("last_price >= 0", name="ck_quotes_last_price_non_negative"),
        sa.CheckConstraint("previous_close >= 0", name="ck_quotes_previous_close_non_negative"),
        sa.CheckConstraint("buy_price IS NULL OR (buy_price >= 0 AND buy_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))", name="ck_quotes_buy_price_valid"),
        sa.CheckConstraint("sell_price IS NULL OR (sell_price >= 0 AND sell_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))", name="ck_quotes_sell_price_valid"),
        sa.CheckConstraint("last_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)", name="ck_quotes_last_price_finite"),
        sa.CheckConstraint("previous_close NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)", name="ck_quotes_previous_close_finite"),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("ticker_id"),
    )
    bind.execute(sa.text("""insert into quotes_new(ticker_id,last_price,previous_close,instrument_status,source_status,error_message,observed_at) select distinct on (p.ticker_id) p.ticker_id,q.last_price,q.previous_close,q.instrument_status,q.source_status,q.error_message,q.observed_at from quotes q join positions p on p.id=q.position_id order by p.ticker_id,q.observed_at desc,q.position_id desc"""))
    for side, prefix in (("BUY", "buy"), ("SELL", "sell")):
        bind.execute(sa.text(f"""
            update quotes_new target set {prefix}_price=source.last_price,
                {prefix}_observed_at=source.observed_at
            from (select distinct on (p.ticker_id) p.ticker_id,q.last_price,q.observed_at
                  from quotes q join positions p on p.id=q.position_id
                  where p.side=:side order by p.ticker_id,q.observed_at desc,q.position_id desc) source
            where target.ticker_id=source.ticker_id
        """), {"side": side})
    op.drop_table("quotes")
    op.rename_table("quotes_new", "quotes")
    op.create_index("ix_quotes_observed_at", "quotes", ["observed_at"])

    op.create_table(
        "option_quotes_new",
        sa.Column("contract_id", sa.Integer(), nullable=False), sa.Column("last_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("previous_close", sa.Numeric(24, 8), nullable=False), sa.Column("underlying_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("instrument_status", sa.String(16), nullable=False), sa.Column("source_status", sa.String(16), nullable=False), sa.Column("error_message", sa.String(250)), sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("last_price >= 0", name="ck_option_quotes_last_price_non_negative"),
        sa.CheckConstraint("previous_close >= 0", name="ck_option_quotes_previous_close_non_negative"),
        sa.CheckConstraint("underlying_price >= 0", name="ck_option_quotes_underlying_price_non_negative"),
        sa.CheckConstraint("last_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)", name="ck_option_quotes_last_price_finite"),
        sa.CheckConstraint("previous_close NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)", name="ck_option_quotes_previous_close_finite"),
        sa.CheckConstraint("underlying_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)", name="ck_option_quotes_underlying_price_finite"),
        sa.ForeignKeyConstraint(["contract_id"], ["option_contracts.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("contract_id"),
    )
    bind.execute(sa.text("""insert into option_quotes_new select distinct on (p.contract_id) p.contract_id,q.last_price,q.previous_close,q.underlying_price,q.instrument_status,q.source_status,q.error_message,q.observed_at from option_quotes q join option_positions p on p.id=q.option_position_id order by p.contract_id,q.observed_at desc,q.option_position_id desc"""))
    op.drop_table("option_quotes")
    op.rename_table("option_quotes_new", "option_quotes")
    op.create_index("ix_option_quotes_observed_at", "option_quotes", ["observed_at"])


def downgrade() -> None:
    raise RuntimeError("A migração de isolamento financeiro não tem downgrade seguro de dados.")
