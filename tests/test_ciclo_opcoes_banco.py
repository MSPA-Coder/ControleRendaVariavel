"""Aportes, realização e reversão de opções conservam quantidade e custo no banco."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    Broker,
    Market,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionType,
    Portfolio,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
    User,
)
from app.options.closure import (
    apply_position_closure,
    create_or_merge_position,
    revert_partial_close,
)

pytestmark = pytest.mark.banco


@pytest.fixture
def cenario(sessao):
    sufixo = uuid4().hex[:8]
    dono = User(username=f"opcao-{sufixo}", password_hash="!")
    outro = User(username=f"outro-{sufixo}", password_hash="!")
    corretora = Broker(name=f"Opções {sufixo}", acronym=sufixo[:6])
    ativo = Ticker(symbol=f"A{sufixo}", trading_name="Objeto", market=Market.B3, currency="BRL", rtd_market_code="B")
    ticker = Ticker(symbol=f"O{sufixo}", trading_name="Contrato", market=Market.B3, currency="BRL", rtd_market_code="B")
    vencimento = OptionExpiration(call_code=sufixo[:5], put_code=sufixo[3:], exercise_date=date(2035, 1, 19))
    sessao.add_all([dono, outro, corretora, ativo, ticker, vencimento])
    sessao.flush()
    carteira = Portfolio(owner_id=dono.id, name=f"Real {sufixo}", currency="BRL", simulated=False)
    simulada = Portfolio(owner_id=dono.id, name=f"Simulada {sufixo}", currency="BRL", simulated=True)
    contrato = OptionContract(ticker_id=ticker.id, underlying_ticker_id=ativo.id, expiration_id=vencimento.id,
                              option_type=OptionType.CALL, strike=Decimal("20"))
    sessao.add_all([carteira, simulada, contrato])
    sessao.flush()
    return SimpleNamespace(dono=dono, outro=outro, corretora=corretora, carteira=carteira,
                           simulada=simulada, contrato=contrato)


def aporte(cenario, quantidade="100", custo="2", simulada=False):
    return OptionPosition(
        owner_id=cenario.dono.id, broker_id=cenario.corretora.id, contract_id=cenario.contrato.id,
        portfolio_id=(cenario.simulada if simulada else cenario.carteira).id,
        quantity=Decimal(quantidade), average_cost=Decimal(custo), side=Side.BUY,
        opened_on=date(2026, 1, 2),
    )


def test_aporte_parcial_reversao_e_total_preservam_custo_e_espelho(sessao, cenario):
    posicao, existente = create_or_merge_position(aporte(cenario))
    reforcada, fundiu = create_or_merge_position(aporte(cenario, "50", "5"))
    sessao.flush()
    assert not existente and fundiu and reforcada.id == posicao.id
    assert posicao.quantity == Decimal("150")
    assert posicao.average_cost == Decimal("3")
    parcial = apply_position_closure(posicao.id, Decimal("4"), date(2026, 1, 10), Decimal("60"), owner_id=cenario.dono.id)
    sessao.flush()
    assert parcial.result == Decimal("60")
    assert posicao.quantity == Decimal("90")
    espelho = sessao.scalars(select(Transaction).where(Transaction.status == TransactionStatus.OPEN)).one()
    assert espelho.quantity == Decimal("90")
    assert espelho.option_contract_id == cenario.contrato.id
    revert_partial_close(parcial)
    sessao.delete(parcial)
    sessao.flush()
    assert posicao.quantity == espelho.quantity == Decimal("150")
    assert posicao.average_cost == Decimal("3")
    identificador = posicao.id
    encerrada = apply_position_closure(identificador, Decimal("4"), date(2026, 1, 11), owner_id=cenario.dono.id)
    sessao.flush()
    assert encerrada.id == espelho.id
    assert encerrada.status is TransactionStatus.CLOSED
    assert encerrada.quantity == Decimal("150")
    assert encerrada.result == Decimal("150")
    assert sessao.get(OptionPosition, identificador) is None
    assert sessao.query(Transaction).count() == 1


def test_outro_dono_nao_encerra_posicao_e_quantidade_excessiva_e_recusada(sessao, cenario):
    posicao, _ = create_or_merge_position(aporte(cenario))
    assert apply_position_closure(posicao.id, Decimal("4"), date(2026, 1, 10), owner_id=cenario.outro.id) is None
    with pytest.raises(ValueError):
        apply_position_closure(posicao.id, Decimal("4"), date(2026, 1, 10), Decimal("101"), owner_id=cenario.dono.id)
    sessao.flush()
    assert posicao.quantity == Decimal("100")
    assert sessao.query(Transaction).filter_by(status=TransactionStatus.CLOSED).count() == 0


def test_simulada_nao_cria_movimento_ou_transacao_e_recusa_fusao_e_fechamento(sessao, cenario):
    posicao, _ = create_or_merge_position(aporte(cenario, simulada=True))
    sessao.flush()
    assert list(posicao.movements) == []
    assert sessao.query(Transaction).count() == 0
    with pytest.raises(ValueError):
        create_or_merge_position(aporte(cenario, simulada=True))
    with pytest.raises(ValueError):
        apply_position_closure(posicao.id, Decimal("4"), date(2026, 1, 10), owner_id=cenario.dono.id)
    assert posicao.quantity == Decimal("100")
    assert sessao.query(Transaction).count() == 0
