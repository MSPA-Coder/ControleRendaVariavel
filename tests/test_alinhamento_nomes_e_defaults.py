"""O esquema das migrações tem os nomes de CHECK e os defaults de produção.

A base de 03/11 saiu de um banco que não era o de produção: 51 CHECK com nome
diferente (prefixo duplicado pela convenção) e 5 defaults a menos. A revisão
`20261111_0043` alinha os dois. Aqui se prova três coisas:

- o banco montado pela cadeia tem os nomes que os modelos declaram, nenhum com
  prefixo duplicado, e os 5 defaults;
- os nomes "de produção" que a revisão renomeia são exatamente os que a
  convenção de nomes geraria para o nome simples (a lista da revisão não tem
  nem a mais nem a menos);
- num banco com a forma de produção (nomes duplicados, sem os defaults) a
  revisão chega ao mesmo resultado, e uma segunda execução não muda nada.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, MetaData, Table, create_engine, text

import app.models  # noqa: F401  (registra as tabelas no metadata)
from app import Base, convention
from tests.conftest import _url_do_banco_de_teste

pytestmark = pytest.mark.banco

REVISAO = (
    Path(__file__).resolve().parent.parent
    / "migrations" / "versions" / "20261111_0043_alinhar_nomes_e_defaults.py"
)

DEFAULTS_ESPERADOS = {
    ("app_settings", "poll_interval_seconds"): "2",
    ("app_settings", "risk_free_rate_annual"): "0.1075",
    ("tickers", "is_benchmark"): "false",
    ("transactions", "status"): "'CLOSED'::transaction_status",
    ("users", "is_active_user"): "true",
}


def _revisao():
    spec = importlib.util.spec_from_file_location("revisao_0043", REVISAO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _preparador():
    engine = create_engine("postgresql+psycopg://")
    return engine.dialect.identifier_preparer


TABELAS_ALINHADAS = {
    "app_settings", "dividends", "option_contracts", "option_position_movements",
    "option_positions", "portfolios", "position_movements", "positions",
    "quote_history", "tickers", "transactions",
}


def _nome_pela_convencao(tabela: str, nome: str) -> str:
    meta = MetaData(naming_convention=convention)
    alvo = Table(tabela, meta)
    restricao = CheckConstraint("true", name=nome)
    alvo.append_constraint(restricao)
    return _preparador().format_constraint(restricao).strip('"')


def _nomes_dos_modelos() -> dict[tuple[str, str], str]:
    """{(tabela, nome da base): nome de produção}, das 11 tabelas.

    Parte do nome lógico de cada CHECK dos modelos (sem prefixo). A base tem a
    convenção aplicada a ele (`ck_<tabela>_<nome>`); produção, a convenção
    aplicada a `ck_<tabela>_<nome>` (prefixo duplicado). Passando de 63
    caracteres o SQLAlchemy trunca e põe um sufixo de hash, que é o que
    aparece em produção (`..._movemen_1cf0`).
    """
    nomes = {}
    for tabela in Base.metadata.tables.values():
        if tabela.name not in TABELAS_ALINHADAS:
            continue
        for c in tabela.constraints:
            if not isinstance(c, CheckConstraint):
                continue
            logico = str(c.name)
            while logico.startswith(f"ck_{tabela.name}_"):
                logico = logico.removeprefix(f"ck_{tabela.name}_")
            base = _nome_pela_convencao(tabela.name, logico)
            producao = _nome_pela_convencao(tabela.name, f"ck_{tabela.name}_{logico}")
            nomes[(tabela.name, base)] = producao
    return nomes


def _catalogo(conexao) -> tuple[set[tuple[str, str]], dict[tuple[str, str], str | None]]:
    nomes = {
        (tabela, nome)
        for tabela, nome in conexao.execute(text(
            "SELECT c.relname, k.conname FROM pg_constraint k "
            "JOIN pg_class c ON c.oid = k.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND k.contype = 'c'"
        ))
        if tabela in TABELAS_ALINHADAS
    }
    defaults = {
        (tabela, coluna): padrao
        for tabela, coluna, padrao in conexao.execute(text(
            "SELECT table_name, column_name, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        ))
    }
    return nomes, defaults


def test_banco_das_migracoes_tem_os_nomes_simples_e_os_defaults(app_com_banco):
    engine = create_engine(_url_do_banco_de_teste(administrativo=True))
    try:
        with engine.connect() as conexao:
            nomes, defaults = _catalogo(conexao)
    finally:
        engine.dispose()

    pares = _revisao().PARES
    assert {(t, novo) for t, _antigo, novo in pares} <= nomes
    assert not {(t, antigo) for t, antigo, _novo in pares} & nomes, "nome de produção na base"
    for chave, esperado in DEFAULTS_ESPERADOS.items():
        assert defaults[chave] == esperado, chave


def test_pares_da_revisao_sao_os_que_a_convencao_antiga_geraria():
    """Cada par (produção, base) é o que a convenção de nomes produz, hash incluído.

    Há outros CHECK, nas mesmas tabelas, já declarados com o prefixo duplicado
    e iguais em produção e na base: não entram. A lista tem 51 pares.
    """
    derivados = {(t, antigo, novo) for (t, novo), antigo in _nomes_dos_modelos().items()}
    pares = _revisao().PARES

    assert len(pares) == 51
    assert len(set(pares)) == 51
    assert set(pares) <= derivados


def test_revisao_leva_um_banco_com_a_forma_de_producao_ao_resultado_certo(app_com_banco):
    revisao = _revisao()
    engine = create_engine(_url_do_banco_de_teste(administrativo=True))
    try:
        with engine.connect() as conexao:
            # DDL do PostgreSQL é transacional: tudo abaixo é desfeito no fim.
            da_base, _ = _catalogo(conexao)
            for tabela, antigo, novo in revisao.PARES:
                conexao.execute(text(
                    f'ALTER TABLE public."{tabela}" RENAME CONSTRAINT "{novo}" TO "{antigo}"'
                ))
            for tabela, coluna in DEFAULTS_ESPERADOS:
                conexao.execute(text(
                    f'ALTER TABLE public."{tabela}" ALTER COLUMN "{coluna}" DROP DEFAULT'
                ))
            antes, defaults_antes = _catalogo(conexao)
            assert antes != da_base
            assert defaults_antes[("users", "is_active_user")] is None

            with Operations.context(MigrationContext.configure(conexao)):
                revisao.upgrade()
            primeiro = _catalogo(conexao)
            with Operations.context(MigrationContext.configure(conexao)):
                revisao.upgrade()
            segundo = _catalogo(conexao)

            assert primeiro[0] == da_base
            for chave, esperado in DEFAULTS_ESPERADOS.items():
                assert primeiro[1][chave] == esperado, chave
            assert segundo == primeiro, "a segunda execução mudou o catálogo"

            with Operations.context(MigrationContext.configure(conexao)):
                revisao.downgrade()
            volta, _ = _catalogo(conexao)
            assert volta == antes
            conexao.rollback()
    finally:
        engine.dispose()


def test_revisao_falha_e_desfaz_se_sobrar_check_com_prefixo_duplicado(app_com_banco):
    """Um CHECK duplicado que a lista não conhece reprova a revisão inteira."""
    revisao = _revisao()
    engine = create_engine(_url_do_banco_de_teste(administrativo=True))
    try:
        with engine.connect() as conexao:
            conexao.execute(text(
                "ALTER TABLE public.positions ADD CONSTRAINT ck_positions_ck_positions_intruso "
                "CHECK (quantity IS NOT NULL)"
            ))
            with pytest.raises(Exception, match="prefixo duplicado"):
                with Operations.context(MigrationContext.configure(conexao)):
                    revisao.upgrade()
            conexao.rollback()
    finally:
        engine.dispose()
