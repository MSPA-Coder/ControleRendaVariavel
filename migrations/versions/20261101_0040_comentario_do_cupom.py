"""A descrição de `leitura.provento` passa a citar o tipo CUPOM (rev. 20261101_0039).

Só o comentário da view muda: quem lê o esquema `leitura` (o MCP de finanças, por
exemplo) aprende os tipos pela descrição.
"""

from alembic import op

revision = "20261101_0040"
down_revision = "20261101_0039"
branch_labels = None
depends_on = None

_COMMENT_ANTIGO = (
    "Proventos recebidos. tipo: DIVIDENDO, JCP ou ALUGUEL; valor na moeda do ativo. "
    "Desde 01/01/2026 vêm do Controle Bancario e valor é o LÍQUIDO que entrou no caixa "
    "(o aluguel já sem IRRF, corretagem e taxas); linhas de planilha que o CB passou a "
    "cobrir não aparecem aqui. yield_on_cost e dividend_yield estão em PONTOS PERCENTUAIS "
    "(0,42 = 0,42%). Os campos opcionais são nulos quando não informados."
)
_COMMENT_NOVO = (
    "Proventos recebidos. tipo: DIVIDENDO, JCP, ALUGUEL ou CUPOM (cupom de título de renda "
    "fixa do exterior; o título fica no CB); valor na moeda do ativo. "
    "Desde 01/01/2026 vêm do Controle Bancario e valor é o LÍQUIDO que entrou no caixa "
    "(o aluguel já sem IRRF, corretagem e taxas); linhas de planilha que o CB passou a "
    "cobrir não aparecem aqui. yield_on_cost e dividend_yield estão em PONTOS PERCENTUAIS "
    "(0,42 = 0,42%). Os campos opcionais são nulos quando não informados."
)


def _is_public_schema() -> bool:
    # Mesma regra da revisão 0027: as views são o contrato do schema `public`.
    return op.get_bind().exec_driver_sql("SELECT current_schema()").scalar() == "public"


def upgrade() -> None:
    if _is_public_schema():
        op.execute(f"COMMENT ON VIEW leitura.provento IS '{_COMMENT_NOVO}'")


def downgrade() -> None:
    if _is_public_schema():
        op.execute(f"COMMENT ON VIEW leitura.provento IS '{_COMMENT_ANTIGO}'")
