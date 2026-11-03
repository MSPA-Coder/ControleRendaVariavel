"""Fator de cotação do ticker e os códigos que ele já teve (aditiva).

Em 03/11/2026 a B3 trocou RAIZ4 por RAIZ55 e passou a cotá-la por lote de 100
ações, sem mudar a quantidade de ninguém (Ofício Circular 022/2026-VPE).

- ``tickers.quote_factor``: quantas unidades a cotação publicada representa. O
  CRV continua guardando tudo por unidade; a cotação que chega é dividida por
  ele. Todo ticker nasce com 1, que é o comportamento de antes.
- ``ticker_former_symbols``: o código anterior, para o que continua chegando
  com ele (CB, myProfit, extratos) achar o mesmo ticker, e o último pregão em
  que valeu, que limita a importação do Yahoo pelo código novo.

Voltar apaga os dois. Com um fator diferente de 1 em uso, as cotações gravadas
seguem por unidade, mas as próximas entrariam por lote: a volta aborta.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261103_0042"
down_revision = "20261101_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickers",
        sa.Column("quote_factor", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_tickers_quote_factor_positive"), "tickers", "quote_factor >= 1"
    )
    op.create_table(
        "ticker_former_symbols",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column("last_trading_day", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticker_id"], ["tickers.id"], name="fk_ticker_former_symbols_ticker_id_tickers", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ticker_former_symbols"),
        sa.UniqueConstraint("symbol", name="uq_ticker_former_symbols_symbol"),
    )
    op.create_index("ix_ticker_former_symbols_ticker_id", "ticker_former_symbols", ["ticker_id"])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM tickers WHERE quote_factor <> 1) THEN "
        "RAISE EXCEPTION 'Há ticker com fator de cotação diferente de 1: volte-o a 1 antes.'; END IF; END $$"
    )
    op.drop_index("ix_ticker_former_symbols_ticker_id", table_name="ticker_former_symbols")
    op.drop_table("ticker_former_symbols")
    op.drop_constraint(op.f("ck_tickers_quote_factor_positive"), "tickers", type_="check")
    op.drop_column("tickers", "quote_factor")
