"""O lado banco do coletor, na parte que não precisa de banco.

O que estes casos protegem é o esquema de chaves sintéticas das opções e o
contrato de fio entre o agente Windows e o servidor. Os dois são silenciosos
quando quebram: uma chave trocada grava o preço do ativo-objeto no lugar do
preço da opção, e nenhuma exceção é levantada.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.collector.database import (
    OptionReading,
    instruments_for,
    option_instrument_keys,
    split_readings,
)
from app.collector.remote_agent import _quotes_payload
from app.collector.rtd import QuoteValue
from app.models import Market, OptionContract, OptionPosition, OptionType, Position, Side, Ticker
from app.routes.collector_agent import _option_reading, _stock_reading

OBSERVADO_EM = datetime(2026, 8, 17, 15, 30, tzinfo=UTC)


def _ticker(ticker_id: int, symbol: str) -> Ticker:
    return Ticker(
        id=ticker_id,
        symbol=symbol,
        trading_name=symbol,
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )


def _posicao(position_id: int, symbol: str) -> Position:
    return Position(id=position_id, ticker_ref=_ticker(position_id * 100, symbol), side=Side.BUY)


def _posicao_de_opcao(option_position_id: int) -> OptionPosition:
    return OptionPosition(
        id=option_position_id,
        contract=OptionContract(
            id=option_position_id,
            option_type=OptionType.CALL,
            strike=Decimal("10"),
            ticker_ref=_ticker(901, "ABCDH100"),
            underlying_ticker_ref=_ticker(902, "ABCD3"),
        ),
    )


def _cotacao(position_id: int, preco: str, *, ultimo_negocio: str | None = None) -> QuoteValue:
    return QuoteValue(
        position_id=position_id,
        last_price=Decimal(preco),
        previous_close=Decimal("9"),
        instrument_status="A",
        observed_at=OBSERVADO_EM,
        last_trade_price=Decimal(ultimo_negocio) if ultimo_negocio is not None else None,
    )


def test_chaves_de_opcao_nunca_colidem_com_uma_posicao_de_acao() -> None:
    opcao, objeto = option_instrument_keys(3)

    assert (opcao, objeto) == (-6, -7)
    assert opcao < 0 and objeto < 0


def test_instrumentos_saem_na_ordem_acao_depois_opcao_e_objeto() -> None:
    instruments, option_keys = instruments_for([_posicao(7, "ABCD3")], [_posicao_de_opcao(3)])

    assert [(item.position_id, item.ticker) for item in instruments] == [
        (7, "ABCD3"),
        (-6, "ABCDH100"),
        (-7, "ABCD3"),
    ]
    assert option_keys == {3: (-6, -7)}


def test_separacao_nao_troca_o_preco_da_opcao_pelo_do_ativo_objeto() -> None:
    valores = [
        _cotacao(7, "30"),
        _cotacao(-6, "2", ultimo_negocio="1.9"),
        _cotacao(-7, "31", ultimo_negocio="30.5"),
    ]

    acoes, opcoes = split_readings(valores, {3: (-6, -7)})

    assert [item.position_id for item in acoes] == [7]
    assert opcoes == [
        OptionReading(
            option_position_id=3,
            option=valores[1],
            underlying_last_price=Decimal("31"),
            underlying_history_price=Decimal("30.5"),
        )
    ]


def test_payload_do_agente_volta_como_a_mesma_leitura_no_servidor() -> None:
    """O contrato de fio, exercitado nas duas pontas.

    O agente serializa; o servidor desconfia e reconstrói. Se um dos lados
    renomear um campo, é aqui que aparece -- em produção apareceria como
    cotação silenciosamente parada.
    """
    valores = [
        _cotacao(7, "30", ultimo_negocio="29.5"),
        _cotacao(-6, "2", ultimo_negocio="1.9"),
        _cotacao(-7, "31", ultimo_negocio="30.5"),
    ]
    payload = _quotes_payload(valores, {3: (-6, -7)})

    acao = _stock_reading(payload["positions"][0])
    opcao = _option_reading(payload["option_positions"][0])

    assert acao.position_id == 7
    assert acao.last_price == Decimal("30")
    assert acao.previous_close == Decimal("9")
    assert acao.instrument_status == "A"
    assert acao.observed_at == OBSERVADO_EM
    assert acao.quote_history_price == Decimal("29.5")

    assert opcao.option_position_id == 3
    assert opcao.option.last_price == Decimal("2")
    assert opcao.option.quote_history_price == Decimal("1.9")
    assert opcao.underlying_last_price == Decimal("31")
    assert opcao.underlying_history_price == Decimal("30.5")


def test_leitura_recusa_cotacao_que_nao_e_objeto() -> None:
    import pytest

    with pytest.raises(ValueError, match="cotação inválida"):
        _stock_reading("nao e um objeto")
    with pytest.raises(ValueError, match="cotação inválida"):
        _option_reading(["tambem nao"])
