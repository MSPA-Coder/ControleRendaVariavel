"""Invariantes que só o PostgreSQL consegue provar.

Fase F1 do LEVANTAMENTO_2026-09.md (achados L01 e L02).

O `AGENTS.md` declara, entre os invariantes financeiros, que "quantidade e
preço médio não são negativos" e que "valores monetários e quantidades
persistidos usam `Decimal`, nunca `float`". As duas garantias moram em
`CheckConstraint` e em colunas `Numeric` -- ou seja, no banco. Enquanto a suíte
recusava a conexão de propósito, nenhuma delas era exercitada: um dublê aceita
`quantity=0` sem reclamar, e aceita `float` sem perder precisão de um jeito que
o `numeric` não perderia.

Este arquivo mede só isso, e não tenta cobertura de domínio. O piso, não o
teto.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError, IntegrityError

from app.models import Broker, Market, Portfolio, Position, Side, Ticker

pytestmark = pytest.mark.banco


# ---------------------------------------------------------------------------
# Cenário mínimo, com objetos reais
# ---------------------------------------------------------------------------


@pytest.fixture
def cenario(sessao):
    corretora = Broker(name="Corretora de teste", acronym="CTST")
    papel = Ticker(
        symbol="TSTE3",
        trading_name="Teste S.A.",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    carteira = Portfolio(name="Carteira de teste", currency="BRL")
    sessao.add_all([corretora, papel, carteira])
    sessao.flush()
    return corretora, papel, carteira


def _posicao(cenario, **campos):
    corretora, papel, carteira = cenario
    padrao = {
        "broker_id": corretora.id,
        "ticker_id": papel.id,
        "portfolio_id": carteira.id,
        "quantity": Decimal("100"),
        "average_cost": Decimal("10.50"),
        "side": Side.BUY,
        "opened_on": date(2026, 6, 10),
    }
    padrao.update(campos)
    return Position(**padrao)


# ---------------------------------------------------------------------------
# 1. As CheckConstraint recusam o que prometem recusar
# ---------------------------------------------------------------------------


def test_banco_recusa_quantidade_zero(sessao, cenario):
    """Invariante: "quantidade e preço médio não são negativos"."""
    sessao.add(_posicao(cenario, quantity=Decimal("0")))
    with pytest.raises(IntegrityError, match="quantity_positive"):
        sessao.flush()


def test_banco_recusa_quantidade_negativa(sessao, cenario):
    sessao.add(_posicao(cenario, quantity=Decimal("-1")))
    with pytest.raises(IntegrityError, match="quantity_positive"):
        sessao.flush()


def test_banco_recusa_custo_medio_negativo(sessao, cenario):
    sessao.add(_posicao(cenario, average_cost=Decimal("-0.01")))
    with pytest.raises(IntegrityError, match="average_cost_non_negative"):
        sessao.flush()


def test_banco_aceita_custo_medio_zero(sessao, cenario):
    """A constraint é `>= 0`, e a diferença importa.

    Custo médio zero é estado legítimo -- ativo recebido em bonificação, por
    exemplo. Se alguém trocar a constraint por `> 0` "para ficar igual à
    quantidade", este teste reprova e explica por quê.
    """
    sessao.add(_posicao(cenario, average_cost=Decimal("0")))
    sessao.flush()  # não levanta


def test_banco_recusa_multiplicador_zero(sessao, cenario):
    sessao.add(_posicao(cenario, quote_multiplier=Decimal("0")))
    with pytest.raises(IntegrityError, match="quote_multiplier_positive"):
        sessao.flush()


def test_banco_recusa_quantidade_nan(sessao, cenario):
    """É para o `NaN` que a constraint `quantity_finite` existe.

    No PostgreSQL, `NaN` é um valor `numeric` perfeitamente armazenável -- e,
    na ordenação de `numeric`, ele é considerado MAIOR que qualquer outro
    valor. Consequência contraintuitiva: `NaN > 0` é **verdadeiro**, e a
    constraint `quantity_positive` deixa o NaN passar sem reclamar.

    Sem a `quantity_finite`, portanto, uma posição com quantidade NaN seria
    gravável, e daí em diante todo total que a somasse viraria NaN em silêncio.
    """
    sessao.add(_posicao(cenario, quantity=Decimal("NaN")))
    with pytest.raises(IntegrityError, match="quantity_finite"):
        sessao.flush()


def test_banco_recusa_quantidade_infinita(sessao, cenario):
    """`Infinity` é barrado antes da constraint, pelo próprio tipo da coluna.

    A primeira versão deste teste esperava `quantity_finite` e reprovou: o
    PostgreSQL recusa antes, com `numeric field overflow -- a field with
    precision 24, scale 8 cannot hold an infinite value`. Ou seja, a defesa
    contra infinito é a precisão declarada, e não a constraint; a constraint
    cuida do NaN, que cabe na coluna.

    O teste fica porque a garantia interessa, mesmo vindo de outro lugar: o
    erro é `DataError` e não `IntegrityError`, e é isso que a aplicação vai
    ver.
    """
    sessao.add(_posicao(cenario, quantity=Decimal("Infinity")))
    with pytest.raises(DataError, match="infinite"):
        sessao.flush()


def test_banco_recusa_moeda_fora_do_vocabulario(sessao):
    sessao.add(Portfolio(name="Carteira em moeda inválida", currency="EUR"))
    with pytest.raises(IntegrityError, match="currency_valid"):
        sessao.flush()


def test_banco_recusa_nome_de_carteira_em_branco(sessao):
    sessao.add(Portfolio(name="   ", currency="BRL"))
    with pytest.raises(IntegrityError, match="name_not_blank"):
        sessao.flush()


# ---------------------------------------------------------------------------
# 2. Decimal, e não float
# ---------------------------------------------------------------------------


def test_quantidade_volta_do_banco_como_decimal_exato(sessao, cenario):
    """Invariante: "valores monetários e quantidades persistidos usam `Decimal`".

    A ida ao banco e a volta é o único jeito de provar que a coluna é `numeric`
    e não `double precision`. Com ponto flutuante, `0.1 + 0.2` guardado e lido
    de volta não fecha em `0.3`; com `numeric`, fecha.
    """
    sessao.add(_posicao(cenario, quantity=Decimal("0.1")))
    sessao.flush()
    sessao.expire_all()

    total = sum(p.quantity for p in sessao.query(Position).all())
    assert isinstance(total, Decimal)
    assert total == Decimal("0.1")


def test_coluna_de_quantidade_e_numeric_no_banco(sessao):
    """Fecha o outro lado: o tipo declarado no PostgreSQL, não no Python.

    Trocar `Numeric` por `Float` no modelo e gerar a migração passaria por toda
    a suíte sem banco. Aqui, não.
    """
    inspetor = inspect(sessao.get_bind())
    colunas = {c["name"]: c for c in inspetor.get_columns("positions")}

    for nome in ("quantity", "average_cost"):
        tipo = str(colunas[nome]["type"]).upper()
        assert "NUMERIC" in tipo, f"positions.{nome} deveria ser NUMERIC, é {tipo}"


# ---------------------------------------------------------------------------
# 3. A cadeia de migrações aplicou de verdade
# ---------------------------------------------------------------------------


def test_migracoes_criaram_as_tabelas(sessao):
    """Se qualquer revisão falhasse ao executar, a fixture `app_com_banco` nem
    teria chegado aqui -- o `upgrade()` acontece antes.

    Esta asserção fecha o outro lado: aplicaram *e* produziram o schema
    esperado, não apenas terminaram sem erro.
    """
    tabelas = set(inspect(sessao.get_bind()).get_table_names())

    esperadas = {"brokers", "tickers", "portfolios", "positions", "alembic_version"}
    faltando = esperadas - tabelas
    assert not faltando, f"migrações não criaram: {sorted(faltando)}"


def test_constraints_de_dominio_existem_no_banco(sessao):
    """Declarar no modelo e esquecer de migrar deixa o código parecendo
    protegido e o banco aceitando qualquer coisa."""
    linhas = sessao.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE connamespace = 'public'::regnamespace"
        )
    ).scalars()
    existentes = set(linhas)

    # Os nomes carregam o prefixo da `naming_convention` declarada em
    # `app/__init__.py` (`ck_<tabela>_<nome>`). Escrever o nome completo aqui
    # e proposital: e assim que ele aparece no banco, no `\d+` do psql e na
    # mensagem de erro que chega a aplicacao.
    esperadas = {
        "ck_positions_quantity_positive",
        "ck_positions_average_cost_non_negative",
        "ck_positions_quote_multiplier_positive",
        "ck_positions_quantity_finite",
        "ck_positions_average_cost_finite",
    }
    faltando = esperadas - existentes
    assert not faltando, (
        f"declaradas nos modelos mas ausentes no banco: {sorted(faltando)}. "
        "Provavelmente falta gerar ou aplicar uma migração."
    )


def _config_alembic():
    """O  deste projeto mora em , nao na raiz.

    Montar a configuracao na mao, em vez de apontar para o arquivo, tira o
    teste da dependencia de onde o Flask-Migrate resolveu colocar o ini.
    """
    from alembic.config import Config

    configuracao = Config()
    configuracao.set_main_option("script_location", "migrations")
    return configuracao


def test_o_banco_esta_na_cabeca_da_cadeia(sessao):
    """`alembic_version` guarda uma revisão só, e é a cabeça do grafo.

    `test_schema_bootstrap.py` já confere que o grafo tem uma cabeça só, lendo
    os arquivos. Aqui a pergunta é outra: o banco que a suíte acabou de
    construir parou nela.
    """
    from alembic.script import ScriptDirectory

    versoes = sessao.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    assert len(versoes) == 1, f"esperava uma revisão aplicada, encontrou {versoes}"

    cabeca = ScriptDirectory.from_config(_config_alembic()).get_current_head()
    assert versoes[0] == cabeca, (
        f"o banco parou em {versoes[0]}, mas a cabeça da cadeia é {cabeca}"
    )
