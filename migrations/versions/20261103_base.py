"""Base do schema: o banco de produção em 03/11/2026, numa revisão só.

Substitui as 42 revisões de 16/08 a 03/11/2026, que continuam no git até o
commit ce22512. O identificador é o da última delas, de propósito: o banco de
produção já está nela, reconhece esta base como aplicada e não precisa de
comando nenhum.

O SQL está em `20261103_base.sql`, gerado por `pg_dump` do banco que a cadeia
antiga montava: estrutura, views do esquema `leitura` com seus comentários, e
as linhas que a cadeia semeava (`app_settings`, vencimentos de opções,
contador do patrimônio). As migrações de dados daquela cadeia não têm efeito
num banco vazio e ficaram de fora. Prova: um banco montado pela cadeia antiga
e outro por esta base deram `pg_dump --schema-only` e `--data-only` idênticos.

O `SELECT` do usuário `mcp_leitura` nas views não está aqui: quem o concede é
o FinancasMCP (`python -m financas_mcp.usuario_leitura`). Revisão nova que
recria uma view `leitura` devolve o `SELECT` a ele, se o papel existir.

Restaurar um dump com `alembic_version` diferente deste identificador exige
antes o código de ce22512 (`flask db upgrade` com ele) e só depois o atual.
"""

from __future__ import annotations

import re
from pathlib import Path

from alembic import op

revision = "20261103_0042"
down_revision = None
branch_labels = None
depends_on = None

SQL = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    # Direto ao cursor do driver, sem parâmetros: o text() do SQLAlchemy leria
    # os ':' de '09:45:00' como parâmetro, e o exec_driver_sql leria o '%'.
    op.get_bind().connection.cursor().execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    texto = SQL.read_text(encoding="utf-8")
    op.execute("DROP SCHEMA IF EXISTS leitura CASCADE")
    for tipo, nome in reversed(re.findall(r"^CREATE (TABLE|FUNCTION|TYPE) (public\.\w+)", texto, re.M)):
        alvo = f"{nome}()" if tipo == "FUNCTION" else nome
        op.execute(f"DROP {tipo} IF EXISTS {alvo} CASCADE")
