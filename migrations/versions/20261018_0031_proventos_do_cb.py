"""Proventos lidos do Controle Bancario: origem, bruto, retencoes e substituicao.

O CB e dono do dinheiro das contas de corretora; de 01/01/2026 em diante o CRV
importa dele os proventos de dividendo, JCP e aluguel de acoes. A revisao so
ACRESCENTA: toda linha existente vira ``origin = 'planilha'`` e nada e apagado.

* ``origin`` / ``origin_id``: de onde a linha veio e, para o CB, o id do
  lancamento de credito (unico por dono, indice parcial);
* ``gross_amount`` / ``withheld_amount``: o credito e o que o aluguel reteve
  (IRRF, corretagem e taxas, liquidos de estornos) ao lado do liquido em
  ``amount``;
* ``superseded_at``: a linha de planilha que o CB passou a cobrir. Fica para
  auditoria e reversao e sai de todo total e publicacao.

``leitura.provento`` (lida pelo FinancasMCP e pelos relatorios) passa a excluir
as linhas substituidas: sem isso o mesmo dinheiro apareceria duas vezes. As
colunas da view nao mudam; so a clausula WHERE. A chave de importacao da
planilha, que impediria dois creditos iguais do CB no mesmo dia, e trocada na
revisao seguinte.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261018_0031"
down_revision = "20261018_0030"
branch_labels = None
depends_on = None

_NAO_FINITO = "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)"

_VIEW_BASE = """CREATE OR REPLACE VIEW leitura.provento AS
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
LEFT JOIN brokers br ON br.id = d.broker_id"""

_COMMENT_ANTIGO = (
    "Proventos recebidos. tipo: DIVIDENDO, JCP ou ALUGUEL; valor na moeda do ativo. "
    "yield_on_cost e dividend_yield estão em PONTOS PERCENTUAIS (0,42 = 0,42%). "
    "Os campos opcionais são nulos quando não informados."
)
_COMMENT_NOVO = (
    "Proventos recebidos. tipo: DIVIDENDO, JCP ou ALUGUEL; valor na moeda do ativo. "
    "Desde 01/01/2026 vêm do Controle Bancario e valor é o LÍQUIDO que entrou no caixa "
    "(o aluguel já sem IRRF, corretagem e taxas); linhas de planilha que o CB passou a "
    "cobrir não aparecem aqui. yield_on_cost e dividend_yield estão em PONTOS PERCENTUAIS "
    "(0,42 = 0,42%). Os campos opcionais são nulos quando não informados."
)


def _is_public_schema() -> bool:
    # Mesma regra da revisao 0027: as views sao o contrato do schema `public`;
    # os testes de migracao em schemas descartaveis nao as recriam.
    return op.get_bind().exec_driver_sql("SELECT current_schema()").scalar() == "public"


def _view(*, filtrar_substituidas: bool, comentario: str) -> None:
    sql = _VIEW_BASE + ("\nWHERE d.superseded_at IS NULL" if filtrar_substituidas else "")
    op.execute(sql)
    op.execute(f"COMMENT ON VIEW leitura.provento IS '{comentario}'")


def upgrade() -> None:
    op.add_column(
        "dividends",
        sa.Column("origin", sa.String(10), nullable=False, server_default="planilha"),
    )
    op.add_column("dividends", sa.Column("origin_id", sa.String(120), nullable=True))
    op.add_column("dividends", sa.Column("gross_amount", sa.Numeric(24, 8), nullable=True))
    op.add_column("dividends", sa.Column("withheld_amount", sa.Numeric(24, 8), nullable=True))
    op.add_column("dividends", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_dividends_superseded_at"), "dividends", ["superseded_at"])

    op.create_check_constraint(op.f("ck_dividends_origin_valid"), "dividends", "origin IN ('planilha', 'cb')")
    op.create_check_constraint(
        op.f("ck_dividends_origin_id_required_for_cb"), "dividends", "origin <> 'cb' OR origin_id IS NOT NULL"
    )
    op.create_check_constraint(
        op.f("ck_dividends_superseded_only_sheet"), "dividends", "superseded_at IS NULL OR origin = 'planilha'"
    )
    op.create_check_constraint(
        op.f("ck_dividends_gross_amount_positive"), "dividends", "gross_amount IS NULL OR gross_amount > 0"
    )
    op.create_check_constraint(
        op.f("ck_dividends_withheld_amount_non_negative"),
        "dividends",
        "withheld_amount IS NULL OR withheld_amount >= 0",
    )
    op.create_check_constraint(
        op.f("ck_dividends_gross_amount_finite"), "dividends", f"gross_amount NOT IN {_NAO_FINITO}"
    )
    op.create_check_constraint(
        op.f("ck_dividends_withheld_amount_finite"), "dividends", f"withheld_amount NOT IN {_NAO_FINITO}"
    )
    op.create_index(
        "uq_dividends_origem_cb",
        "dividends",
        ["owner_id", "origin_id"],
        unique=True,
        postgresql_where=sa.text("origin = 'cb'"),
    )

    if _is_public_schema():
        _view(filtrar_substituidas=True, comentario=_COMMENT_NOVO)


def downgrade() -> None:
    # A view le `superseded_at` no WHERE: ela volta ao texto antigo antes de a
    # coluna sair, senao o PostgreSQL recusa (de proposito).
    if _is_public_schema():
        _view(filtrar_substituidas=False, comentario=_COMMENT_ANTIGO)
    op.drop_index("uq_dividends_origem_cb", table_name="dividends")
    for nome in (
        "ck_dividends_withheld_amount_finite",
        "ck_dividends_gross_amount_finite",
        "ck_dividends_withheld_amount_non_negative",
        "ck_dividends_gross_amount_positive",
        "ck_dividends_superseded_only_sheet",
        "ck_dividends_origin_id_required_for_cb",
        "ck_dividends_origin_valid",
    ):
        op.drop_constraint(op.f(nome), "dividends", type_="check")
    op.drop_index(op.f("ix_dividends_superseded_at"), table_name="dividends")
    for coluna in ("superseded_at", "withheld_amount", "gross_amount", "origin_id", "origin"):
        op.drop_column("dividends", coluna)
