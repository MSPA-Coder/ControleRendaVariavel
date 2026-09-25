"""Período de importação de cotações: do primeiro ao último dia em que o
ticker esteve na carteira, e não só enquanto há posição aberta."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models import (
    Broker,
    Market,
    Portfolio,
    Position,
    PositionLedgerArchive,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
    User,
)
from app.quotes import history

HOJE = date.today()


class _Rows(list):
    def all(self):
        return list(self)


def _ticker(ticker_id, symbol, *, is_benchmark=False):
    return SimpleNamespace(id=ticker_id, symbol=symbol, market=Market.B3, is_benchmark=is_benchmark)


def _executar(monkeypatch, *, abertas=(), opcoes=(), operacoes=(), arquivo=(), tickers=()):
    """As quatro fontes de `_held_periods`, na ordem, e depois os tickers."""
    respostas = iter([list(abertas), list(opcoes), list(operacoes), list(arquivo), _Rows(tickers)])
    monkeypatch.setattr(history.db.session, "execute", lambda _statement: next(respostas))


def _periodos(targets):
    return {target.symbol: (start, end) for target, start, end in targets}


def test_operacao_encerrada_antes_da_posicao_atual_puxa_o_inicio(monkeypatch):
    """O caso do CGC: uma operação de fevereiro, encerrada, e a posição de junho."""
    _executar(
        monkeypatch,
        abertas=[(1, date(2026, 6, 16), None)],
        operacoes=[(1, date(2026, 2, 11), date(2026, 3, 5))],
        tickers=[_ticker(1, "CGC")],
    )

    assert _periodos(history.quote_update_targets()) == {"CGC": (date(2026, 2, 11), HOJE)}


def test_ativo_encerrado_vai_ate_o_ultimo_encerramento(monkeypatch):
    """O caso do HODL11: sem posição aberta ele sumia da importação."""
    _executar(
        monkeypatch,
        operacoes=[(2, date(2026, 3, 23), date(2026, 5, 2)), (2, date(2026, 6, 1), date(2026, 8, 28))],
        arquivo=[(2, date(2026, 8, 20), date(2026, 8, 20))],
        tickers=[_ticker(2, "HODL11")],
    )

    assert _periodos(history.quote_update_targets()) == {"HODL11": (date(2026, 3, 23), date(2026, 8, 28))}


def test_operacao_ainda_aberta_vai_ate_hoje(monkeypatch):
    # `closed_on` nulo chega do banco já trocado por hoje (`coalesce`).
    _executar(monkeypatch, operacoes=[(3, date(2026, 1, 5), HOJE)], tickers=[_ticker(3, "PETR4")])

    assert _periodos(history.quote_update_targets()) == {"PETR4": (date(2026, 1, 5), HOJE)}


def test_benchmark_cobre_a_carteira_inteira_ate_hoje(monkeypatch):
    _executar(
        monkeypatch,
        abertas=[(1, date(2026, 6, 16), None)],
        opcoes=[(4, date(2025, 11, 7), None)],
        operacoes=[(2, date(2024, 1, 10), date(2024, 5, 1))],
        tickers=[_ticker(1, "CGC"), _ticker(2, "HODL11"), _ticker(4, "RAIZH150"), _ticker(9, "BOVA11", is_benchmark=True)],
    )

    periodos = _periodos(history.quote_update_targets())

    assert periodos["BOVA11"] == (date(2024, 1, 10), HOJE)
    assert periodos["RAIZH150"] == (date(2025, 11, 7), HOJE)
    assert list(periodos) == ["BOVA11", "CGC", "HODL11", "RAIZH150"]


def test_sem_nenhuma_posicao_benchmark_usa_o_lookback(monkeypatch):
    _executar(monkeypatch, tickers=[_ticker(9, "BOVA11", is_benchmark=True)])

    ((target, start, end),) = history.quote_update_targets()

    assert target.symbol == "BOVA11"
    assert start == HOJE - timedelta(days=history.DEFAULT_BENCHMARK_IMPORT_LOOKBACK_DAYS)
    assert end == HOJE


def test_atualizacao_diaria_deixa_de_fora_o_ativo_ja_encerrado(monkeypatch):
    _executar(
        monkeypatch,
        abertas=[(1, date(2026, 6, 16), None)],
        operacoes=[(2, date(2026, 3, 23), date(2026, 8, 28))],
        tickers=[_ticker(1, "CGC"), _ticker(2, "HODL11"), _ticker(9, "BOVA11", is_benchmark=True)],
    )

    assert [target.symbol for target in history.quote_update_target_tickers()] == ["BOVA11", "CGC"]


@pytest.mark.banco
def test_periodos_lidos_do_banco(sessao):
    """O mesmo cenário no PostgreSQL: o SQL (junções, `coalesce`, data nula)
    é a parte que as simulações acima não exercitam."""
    dono = User(username="dono-cotacoes", password_hash="hash")
    corretora = Broker(name="Genial", acronym="GNL")
    carteira = Portfolio(name="BRL", owner_ref=dono, currency="BRL", simulated=False)
    cgc = Ticker(symbol="CGC", trading_name="CGC", market=Market.NASDAQ, rtd_market_code="N", currency="USD")
    hodl = Ticker(symbol="HODL11", trading_name="HODL11", market=Market.B3, rtd_market_code="B", currency="BRL")
    sessao.add_all([dono, corretora, carteira, cgc, hodl])
    sessao.flush()
    comum = {"owner_id": dono.id, "broker_id": corretora.id, "portfolio_id": carteira.id}
    sessao.add_all(
        [
            Position(**comum, ticker_id=cgc.id, quantity=Decimal("10"), average_cost=Decimal("1"),
                     side=Side.BUY, opened_on=date(2026, 6, 16)),
            Transaction(**comum, ticker_id=cgc.id, quantity=Decimal("5"), average_cost=Decimal("1"),
                        exit_price=Decimal("1.1"), side=Side.BUY, opened_on=date(2026, 2, 11),
                        closed_on=date(2026, 3, 5), result=Decimal("0.5"),
                        status=TransactionStatus.CLOSED),
            Transaction(**comum, ticker_id=hodl.id, quantity=Decimal("5"), average_cost=Decimal("80"),
                        exit_price=Decimal("90"), side=Side.BUY, opened_on=date(2026, 3, 23),
                        closed_on=date(2026, 8, 28), result=Decimal("50"),
                        status=TransactionStatus.CLOSED),
            PositionLedgerArchive(owner_id=dono.id, occurred_on=date(2026, 3, 23), ticker_id=hodl.id,
                                  portfolio_id=carteira.id, broker_id=corretora.id, instrument="stock",
                                  source_position_id=987, resulting_signed_quantity=Decimal("5")),
        ]
    )
    sessao.flush()

    periodos = _periodos(history.quote_update_targets())

    assert periodos["CGC"] == (date(2026, 2, 11), HOJE)
    assert periodos["HODL11"] == (date(2026, 3, 23), date(2026, 8, 28))
