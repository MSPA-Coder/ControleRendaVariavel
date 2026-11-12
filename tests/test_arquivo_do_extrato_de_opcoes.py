"""A revisão `20261111_0046` cria `option_movement_archive` e se confere sozinha.

Provas:

- o banco montado pela cadeia tem a tabela com as colunas, os CHECK e os índices
  que o modelo declara (nenhum a mais, nenhum a menos);
- a revisão é idempotente (duas execuções dão o mesmo catálogo), o `downgrade`
  apaga a tabela e o gatilho, e subir de novo a recria;
- uma tabela preexistente com outra forma reprova a revisão (autoverificação);
- a tabela não tem gatilho do outbox do patrimônio v4 (o feed publica só o arquivo
  das ações) e a leitura do MCP (`mcp_leitura`) não alcança a tabela.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, create_engine, text

import app.models  # noqa: F401  (registra as tabelas no metadata)
from app import Base
from tests.conftest import _url_do_banco_de_teste

pytestmark = pytest.mark.banco

REVISAO = (
    Path(__file__).resolve().parent.parent
    / "migrations" / "versions" / "20261111_0046_arquivo_do_extrato_de_opcoes.py"
)
TABELA = "option_movement_archive"


def _revisao():
    spec = importlib.util.spec_from_file_location("revisao_0046", REVISAO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _catalogo(conexao) -> dict[str, list]:
    def linhas(sql: str) -> list:
        return [tuple(r) for r in conexao.execute(text(sql), {"t": TABELA})]

    return {
        "colunas": linhas(
            "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"
        ),
        "restricoes": linhas(
            "SELECT k.conname, pg_get_constraintdef(k.oid) FROM pg_constraint k "
            "JOIN pg_class c ON c.oid = k.conrelid WHERE c.relname = :t ORDER BY k.conname"
        ),
        "indices": linhas(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = :t ORDER BY indexname"
        ),
        "gatilhos": linhas(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = to_regclass('public.' || :t) "
            "AND NOT tgisinternal ORDER BY tgname"
        ),
    }


def _executar(conexao, funcao) -> None:
    with Operations.context(MigrationContext.configure(conexao)):
        funcao()


def test_banco_das_migracoes_tem_a_tabela_como_o_modelo_declara(app_com_banco):
    tabela = Base.metadata.tables[TABELA]
    engine = create_engine(_url_do_banco_de_teste(administrativo=True))
    try:
        with engine.connect() as conexao:
            catalogo = _catalogo(conexao)
    finally:
        engine.dispose()

    assert [c[0] for c in catalogo["colunas"]] == [c.name for c in tabela.columns]
    nulos = {c[0]: c[2] == "YES" for c in catalogo["colunas"]}
    assert nulos == {c.name: c.nullable for c in tabela.columns}
    nomes = {r[0] for r in catalogo["restricoes"]}
    checks_do_modelo = {str(c.name) for c in tabela.constraints if isinstance(c, CheckConstraint)}
    assert checks_do_modelo <= nomes
    assert len(checks_do_modelo) == len(_revisao().CHECKS)
    assert {i[0] for i in catalogo["indices"] if i[0].startswith("ix_")} == set(_revisao().INDICES)
    assert {i.name for i in tabela.indexes} == set(_revisao().INDICES)
    assert catalogo["gatilhos"] == []  # o feed v4 publica só o arquivo das ações


def test_revisao_e_idempotente_o_downgrade_apaga_e_subir_de_novo_recria(app_com_banco):
    revisao = _revisao()
    engine = create_engine(_url_do_banco_de_teste(administrativo=True))
    try:
        with engine.connect() as conexao:
            # DDL do PostgreSQL é transacional: tudo abaixo é desfeito no fim.
            da_base = _catalogo(conexao)
            _executar(conexao, revisao.upgrade)
            assert _catalogo(conexao) == da_base, "a segunda execução mudou o catálogo"

            _executar(conexao, revisao.downgrade)
            assert conexao.scalar(text("SELECT to_regclass('public.option_movement_archive')")) is None
            _executar(conexao, revisao.downgrade)  # também idempotente

            _executar(conexao, revisao.upgrade)
            assert _catalogo(conexao) == da_base
            conexao.rollback()
    finally:
        engine.dispose()


def test_coluna_a_mais_numa_tabela_preexistente_reprova_a_revisao(app_com_banco):
    revisao = _revisao()
    engine = create_engine(_url_do_banco_de_teste(administrativo=True))
    try:
        with engine.connect() as conexao:
            conexao.execute(text(f"ALTER TABLE public.{TABELA} ADD COLUMN intrusa integer"))
            with pytest.raises(Exception, match="colunas inesperadas"):
                _executar(conexao, revisao.upgrade)
            conexao.rollback()
    finally:
        engine.dispose()


def test_check_a_mais_numa_tabela_preexistente_reprova_a_revisao(app_com_banco):
    revisao = _revisao()
    engine = create_engine(_url_do_banco_de_teste(administrativo=True))
    try:
        with engine.connect() as conexao:
            conexao.execute(text(
                f"ALTER TABLE public.{TABELA} ADD CONSTRAINT ck_{TABELA}_intruso CHECK (price IS NOT NULL)"
            ))
            with pytest.raises(Exception, match="CHECK inesperados"):
                _executar(conexao, revisao.upgrade)
            conexao.rollback()
    finally:
        engine.dispose()


def test_mcp_leitura_nao_alcanca_a_tabela_e_a_aplicacao_escreve_nela(app_com_banco):
    engine = create_engine(_url_do_banco_de_teste(administrativo=True))
    try:
        with engine.connect() as conexao:
            papeis = {r[0] for r in conexao.execute(text("SELECT rolname FROM pg_roles"))}
            if "mcp_leitura" in papeis:
                for privilegio in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    assert not conexao.scalar(
                        text("SELECT has_table_privilege('mcp_leitura', 'public.option_movement_archive', :p)"),
                        {"p": privilegio},
                    ), privilegio
            if "investimentos_app" in papeis:
                assert conexao.scalar(text(
                    "SELECT has_table_privilege('investimentos_app', 'public.option_movement_archive', 'INSERT')"
                ))
    finally:
        engine.dispose()
