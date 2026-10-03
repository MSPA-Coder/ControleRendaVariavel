"""O esquema `leitura` publica as regras do CRV para quem lê o banco de fora.

O FinancasMCP lia as tabelas direto e reescrevia em SQL as regras da carteira:
que posição está aberta, qual preço vale, o sinal de uma posição vendida. Em
24/09 uma coluna removida quebrou o `crv_carteira` em produção. As views
carregam a regra, e este arquivo prova duas coisas:

1. as views dão os números que o domínio dá, em cenários montados à mão;
2. o PostgreSQL recusa mexer numa coluna que uma view lê, de modo que a quebra
   de schema reprova a migração de quem mexeu, e não o leitor em produção.

As views pertencem ao papel administrativo, e o papel restrito da aplicação não
as enxerga (de propósito). Por isso a sessão daqui usa o papel administrativo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import create_app, db
from app.models import (
    Broker,
    Dividend,
    IncomeKind,
    Market,
    OptionContract,
    OptionExpiration,
    OptionType,
    Portfolio,
    Position,
    Quote,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
    User,
)
from tests.conftest import _url_do_banco_de_teste

pytestmark = pytest.mark.banco

VIEWS_ESPERADAS = {
    "ativo",
    "carteira",
    "carteira_ativo",
    "corretora",
    "cotacao",
    "cotacao_historico",
    "cotacao_opcao",
    "movimento_opcao",
    "movimento_posicao",
    "opcao",
    "operacao",
    "posicao",
    "posicao_historica",
    "provento",
}


@pytest.fixture
def sessao_admin(app_com_banco):
    """Sessão do papel administrativo, dentro de uma transação sempre desfeita."""
    aplicacao = create_app(
        {"SQLALCHEMY_DATABASE_URI": _url_do_banco_de_teste(administrativo=True), "TESTING": True}
    )
    with aplicacao.app_context():
        try:
            yield db.session
        finally:
            db.session.rollback()
            db.session.remove()


@pytest.fixture
def cenario(sessao_admin):
    usuario = User(username="leitura", password_hash="hash")
    corretora = Broker(name="Corretora leitura", acronym="CLTR")
    comprada = Ticker(symbol="LTRA3", trading_name="Leitura A", market=Market.B3, rtd_market_code="B", currency="BRL")
    vendida = Ticker(symbol="LTRB3", trading_name="Leitura B", market=Market.B3, rtd_market_code="B", currency="BRL")
    sem_cotacao = Ticker(symbol="LTRC3", trading_name="Leitura C", market=Market.B3, rtd_market_code="B", currency="BRL")
    real = Portfolio(name="Carteira real leitura", owner_ref=usuario, currency="BRL")
    simulada = Portfolio(name="Carteira simulada leitura", owner_ref=usuario, currency="BRL", simulated=True)
    sessao_admin.add_all([usuario, corretora, comprada, vendida, sem_cotacao, real, simulada])
    sessao_admin.flush()
    agora = datetime(2026, 10, 2, 17, 0, tzinfo=UTC)
    sessao_admin.add_all(
        [
            Quote(ticker_id=comprada.id, last_price=Decimal("12.50"), previous_close=Decimal("12.00"), observed_at=agora),
            Quote(ticker_id=vendida.id, last_price=Decimal("8.00"), previous_close=Decimal("7.50"), observed_at=agora),
        ]
    )
    sessao_admin.flush()
    return {
        "usuario": usuario, "corretora": corretora, "real": real, "simulada": simulada,
        "comprada": comprada, "vendida": vendida, "sem_cotacao": sem_cotacao,
    }


def _posicao(c, ticker, carteira, *, lado, quantidade, custo):
    return Position(
        owner_id=c["usuario"].id, broker_id=c["corretora"].id, ticker_id=ticker.id,
        portfolio_id=carteira.id, quantity=Decimal(quantidade), average_cost=Decimal(custo),
        side=lado, opened_on=date(2026, 6, 10),
    )


def _consulta(sessao, sql, **parametros):
    return sessao.execute(text(sql), parametros).all()


def test_o_esquema_publica_todas_as_views(sessao_admin):
    existentes = {
        linha[0]
        for linha in _consulta(
            sessao_admin, "SELECT table_name FROM information_schema.views WHERE table_schema = 'leitura'"
        )
    }
    assert existentes == VIEWS_ESPERADAS


def test_as_views_com_regra_de_negocio_tem_comentario(sessao_admin):
    comentadas = {
        linha[0]
        for linha in _consulta(
            sessao_admin,
            """
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'leitura' AND c.relkind = 'v' AND obj_description(c.oid, 'pg_class') IS NOT NULL
            """,
        )
    }
    assert {"posicao", "operacao", "provento", "cotacao"} <= comentadas


def test_posicao_valor_de_mercado_e_resultado_com_o_sinal_do_lado(sessao_admin, cenario):
    c = cenario
    sessao_admin.add_all(
        [
            _posicao(c, c["comprada"], c["real"], lado=Side.BUY, quantidade="100", custo="10.00"),
            _posicao(c, c["vendida"], c["real"], lado=Side.SELL, quantidade="50", custo="9.00"),
        ]
    )
    sessao_admin.flush()

    linhas = {
        linha[0]: linha[1:]
        for linha in _consulta(
            sessao_admin,
            """
            SELECT ativo, lado, quantidade, custo_total, preco, valor_mercado, resultado_aberto, instrumento
            FROM leitura.posicao WHERE carteira = 'Carteira real leitura'
            """,
        )
    }
    # Comprada: 100 x 12,50 = 1250; resultado (12,50 - 10,00) x 100 = +250.
    assert linhas["LTRA3"] == ("BUY", Decimal("100"), Decimal("1000.00"), Decimal("12.50"), Decimal("1250.00"), Decimal("250.00"), "acao")
    # Vendida: sinal negativo. Valor -50 x 8,00 = -400; resultado -(8,00 - 9,00) x 50 = +50.
    assert linhas["LTRB3"] == ("SELL", Decimal("50"), Decimal("450.00"), Decimal("8.00"), Decimal("-400.00"), Decimal("50.00"), "acao")


def test_posicao_sem_cotacao_aparece_sem_preco_e_sem_valor_de_mercado(sessao_admin, cenario):
    c = cenario
    sessao_admin.add(_posicao(c, c["sem_cotacao"], c["real"], lado=Side.BUY, quantidade="10", custo="5.00"))
    sessao_admin.flush()

    (preco, valor, resultado, custo_total) = _consulta(
        sessao_admin,
        "SELECT preco, valor_mercado, resultado_aberto, custo_total FROM leitura.posicao WHERE ativo = 'LTRC3'",
    )[0]
    assert (preco, valor, resultado) == (None, None, None)
    assert custo_total == Decimal("50.00")


def test_posicao_de_carteira_simulada_vem_marcada(sessao_admin, cenario):
    c = cenario
    sessao_admin.add(_posicao(c, c["comprada"], c["simulada"], lado=Side.BUY, quantidade="10", custo="10.00"))
    sessao_admin.flush()

    simulada = _consulta(
        sessao_admin, "SELECT simulada FROM leitura.posicao WHERE carteira = 'Carteira simulada leitura'"
    )
    assert simulada == [(True,)]


def test_operacao_fechada_traz_o_resultado_bruto_gravado(sessao_admin, cenario):
    c = cenario
    sessao_admin.add(
        Transaction(
            owner_id=c["usuario"].id, broker_id=c["corretora"].id, ticker_id=c["comprada"].id,
            portfolio_id=c["real"].id, quantity=Decimal("10"), average_cost=Decimal("10.00"),
            exit_price=Decimal("11.00"), side=Side.BUY, opened_on=date(2026, 5, 1),
            closed_on=date(2026, 5, 20), status=TransactionStatus.CLOSED, result=Decimal("10.00"),
        )
    )
    sessao_admin.flush()

    (lado, status, resultado, saida, e_opcao) = _consulta(
        sessao_admin,
        "SELECT lado, status, resultado, preco_de_saida, e_opcao FROM leitura.operacao WHERE ativo = 'LTRA3'",
    )[0]
    assert (lado, status, resultado, saida, e_opcao) == ("BUY", "CLOSED", Decimal("10.00"), Decimal("11.00"), False)


def test_operacao_de_opcao_aparece_com_o_codigo_da_opcao_e_o_ativo_objeto(sessao_admin, cenario):
    """A operação de opção não tem `ticker_id`: o SQL antigo do MCP a descartava em silêncio."""
    c = cenario
    opcao = Ticker(symbol="LTRAK120", trading_name="Opção leitura", market=Market.B3, rtd_market_code="B", currency="BRL")
    vencimento = OptionExpiration(call_code="LK", put_code="LW", exercise_date=date(2026, 11, 20))
    sessao_admin.add_all([opcao, vencimento])
    sessao_admin.flush()
    contrato = OptionContract(
        ticker_id=opcao.id, underlying_ticker_id=c["comprada"].id, expiration_id=vencimento.id,
        option_type=OptionType.CALL, strike=Decimal("12.00"),
    )
    sessao_admin.add(contrato)
    sessao_admin.flush()
    sessao_admin.add(
        Transaction(
            owner_id=c["usuario"].id, broker_id=c["corretora"].id, option_contract_id=contrato.id,
            portfolio_id=c["real"].id, quantity=Decimal("100"), average_cost=Decimal("0.50"),
            exit_price=Decimal("0.80"), side=Side.SELL, opened_on=date(2026, 9, 1),
            closed_on=date(2026, 9, 10), status=TransactionStatus.CLOSED, result=Decimal("30.00"),
        )
    )
    sessao_admin.flush()

    (ativo, objeto, e_opcao, resultado) = _consulta(
        sessao_admin, "SELECT ativo, objeto, e_opcao, resultado FROM leitura.operacao WHERE e_opcao"
    )[0]
    assert (ativo, objeto, e_opcao, resultado) == ("LTRAK120", "LTRA3", True, Decimal("30.00"))


def test_provento_traz_o_tipo_e_o_valor(sessao_admin, cenario):
    c = cenario
    sessao_admin.add(
        Dividend(
            owner_id=c["usuario"].id, kind=IncomeKind.JCP, broker_id=c["corretora"].id,
            ticker_id=c["comprada"].id, amount=Decimal("42.00"), payment_date=date(2026, 9, 15),
        )
    )
    sessao_admin.flush()

    (tipo, valor, pago_em, moeda, opcional) = _consulta(
        sessao_admin,
        "SELECT tipo, valor, pago_em, moeda, data_com FROM leitura.provento WHERE ativo = 'LTRA3'",
    )[0]
    assert (tipo, valor, pago_em, moeda, opcional) == ("JCP", Decimal("42.00"), date(2026, 9, 15), "BRL", None)


@pytest.mark.parametrize(
    "alteracao",
    [
        "ALTER TABLE positions DROP COLUMN average_cost",
        "ALTER TABLE quotes DROP COLUMN last_price",
        "ALTER TABLE dividends DROP COLUMN payment_date",
        "ALTER TABLE tickers ALTER COLUMN symbol TYPE text",
    ],
)
def test_postgres_recusa_mexer_em_coluna_que_uma_view_le(sessao_admin, alteracao):
    """Quem alterar uma coluna lida precisa antes cuidar da view. Esse é o ponto."""
    with pytest.raises(DBAPIError, match="depend"), sessao_admin.begin_nested():
        sessao_admin.execute(text(alteracao))
