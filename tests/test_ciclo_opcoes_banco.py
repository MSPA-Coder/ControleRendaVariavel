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
    OptionPositionMovement,
    OptionType,
    Portfolio,
    Position,
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
from app.positions.closure import apply_position_closure as encerrar_acao
from app.positions.closure import create_or_merge_position as aportar_acao

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
    carteira = Portfolio(owner_id=dono.id, name=f"Real {sufixo}", currency="BRL")
    contrato = OptionContract(ticker_id=ticker.id, underlying_ticker_id=ativo.id, expiration_id=vencimento.id,
                              option_type=OptionType.CALL, strike=Decimal("20"))
    sessao.add_all([carteira, contrato])
    sessao.flush()
    return SimpleNamespace(dono=dono, outro=outro, corretora=corretora, carteira=carteira,
                           contrato=contrato)


def aporte(cenario, quantidade="100", custo="2"):
    return OptionPosition(
        owner_id=cenario.dono.id, broker_id=cenario.corretora.id, contract_id=cenario.contrato.id,
        portfolio_id=cenario.carteira.id,
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


@pytest.mark.parametrize(("lado", "sinal"), [(Side.BUY, 1), (Side.SELL, -1)])
def test_reverter_primeiro_encerramento_recalcula_venda_posterior(sessao, cenario, lado, sinal):
    abertura = aporte(cenario, "100", "2")
    abertura.side = lado
    posicao, _ = create_or_merge_position(abertura)
    primeira = apply_position_closure(posicao.id, Decimal("4"), date(2026, 1, 10), Decimal("50"), owner_id=cenario.dono.id)
    reforco = aporte(cenario, "100", "5")
    reforco.side = lado
    reforco.opened_on = date(2026, 1, 12)
    create_or_merge_position(reforco)
    segunda = apply_position_closure(posicao.id, Decimal("8"), date(2026, 1, 15), Decimal("30"), owner_id=cenario.dono.id)
    sessao.flush()
    assert segunda.average_cost == Decimal("4") and segunda.result == Decimal("120") * sinal

    revert_partial_close(primeira)
    sessao.delete(primeira)
    sessao.flush()
    sessao.expire_all()

    posicao_atual = sessao.get(OptionPosition, posicao.id)
    venda_atual = sessao.get(Transaction, segunda.id)
    movimento = sessao.scalar(select(OptionPositionMovement).where(OptionPositionMovement.transaction_id == segunda.id))
    assert posicao_atual.quantity == Decimal("170") and posicao_atual.average_cost == Decimal("3.5")
    assert venda_atual.average_cost == Decimal("3.5") and venda_atual.result == Decimal("135") * sinal
    assert movimento.result == venda_atual.result


@pytest.mark.parametrize("instrumento", ["acao", "opcao"])
def test_encerramento_retroativo_recalcula_custo_historico(sessao, cenario, instrumento):
    def novo(quantidade, custo, data):
        if instrumento == "opcao":
            candidato = aporte(cenario, quantidade, custo)
        else:
            candidato = Position(owner_id=cenario.dono.id, broker_id=cenario.corretora.id,
                                 ticker_id=cenario.contrato.underlying_ticker_id, portfolio_id=cenario.carteira.id,
                                 quantity=Decimal(quantidade), average_cost=Decimal(custo), side=Side.BUY)
        candidato.opened_on = data
        return candidato

    registrar = create_or_merge_position if instrumento == "opcao" else aportar_acao
    encerrar = apply_position_closure if instrumento == "opcao" else encerrar_acao
    posicao, _ = registrar(novo("100", "2", date(2026, 1, 2)))
    registrar(novo("100", "5", date(2026, 1, 10)))
    posterior = encerrar(posicao.id, Decimal("8"), date(2026, 1, 15), Decimal("30"), owner_id=cenario.dono.id)
    transacao = encerrar(posicao.id, Decimal("8"), date(2026, 1, 5), Decimal("50"), owner_id=cenario.dono.id)
    sessao.flush()
    sessao.expire_all()

    atual = sessao.get(type(posicao), posicao.id)
    venda = sessao.get(Transaction, transacao.id)
    venda_posterior = sessao.get(Transaction, posterior.id)
    assert venda.average_cost == Decimal("2") and venda.result == Decimal("300")
    assert venda_posterior.average_cost == Decimal("4") and venda_posterior.result == Decimal("120")
    assert atual.quantity == Decimal("120") and atual.average_cost == Decimal("4")


@pytest.mark.parametrize("instrumento", ["acao", "opcao"])
@pytest.mark.parametrize("quantidade", [Decimal("120"), None])
def test_encerramento_retroativo_sem_saldo_na_data_e_recusado(sessao, cenario, instrumento, quantidade):
    if instrumento == "opcao":
        registrar, encerrar = create_or_merge_position, apply_position_closure
        abertura, reforco = aporte(cenario, "100", "2"), aporte(cenario, "100", "5")
    else:
        registrar, encerrar = aportar_acao, encerrar_acao
        atributos = {"owner_id": cenario.dono.id, "broker_id": cenario.corretora.id,
                     "ticker_id": cenario.contrato.underlying_ticker_id,
                     "portfolio_id": cenario.carteira.id, "side": Side.BUY}
        abertura = Position(**atributos, quantity=Decimal("100"), average_cost=Decimal("2"))
        reforco = Position(**atributos, quantity=Decimal("100"), average_cost=Decimal("5"))
    abertura.opened_on, reforco.opened_on = date(2026, 1, 2), date(2026, 1, 10)
    posicao, _ = registrar(abertura)
    registrar(reforco)
    with pytest.raises(ValueError), sessao.begin_nested():
        encerrar(posicao.id, Decimal("8"), date(2026, 1, 5), quantidade, owner_id=cenario.dono.id)
    sessao.refresh(posicao)
    assert posicao.quantity == Decimal("200")


@pytest.mark.parametrize("instrumento", ["acao", "opcao"])
def test_encerramento_retroativo_que_invalida_baixa_posterior_e_recusado(sessao, cenario, instrumento):
    if instrumento == "opcao":
        registrar, encerrar = create_or_merge_position, apply_position_closure
        abertura, reforco = aporte(cenario, "100", "2"), aporte(cenario, "100", "5")
    else:
        registrar, encerrar = aportar_acao, encerrar_acao
        atributos = {"owner_id": cenario.dono.id, "broker_id": cenario.corretora.id,
                     "ticker_id": cenario.contrato.underlying_ticker_id,
                     "portfolio_id": cenario.carteira.id, "side": Side.BUY}
        abertura = Position(**atributos, quantity=Decimal("100"), average_cost=Decimal("2"))
        reforco = Position(**atributos, quantity=Decimal("100"), average_cost=Decimal("5"))
    abertura.opened_on, reforco.opened_on = date(2026, 1, 2), date(2026, 1, 15)
    posicao, _ = registrar(abertura)
    anterior = encerrar(posicao.id, Decimal("8"), date(2026, 1, 10), Decimal("70"), owner_id=cenario.dono.id)
    registrar(reforco)
    with pytest.raises(ValueError), sessao.begin_nested():
        encerrar(posicao.id, Decimal("8"), date(2026, 1, 5), Decimal("50"), owner_id=cenario.dono.id)
    sessao.refresh(posicao)
    assert posicao.quantity == Decimal("130")
    assert sessao.get(Transaction, anterior.id).quantity == Decimal("70")


@pytest.mark.parametrize("instrumento", ["acao", "opcao"])
@pytest.mark.parametrize("antes_abertura", [False, True])
def test_aporte_retroativo_recalcula_encerramento_posterior(sessao, cenario, instrumento, antes_abertura):
    if instrumento == "opcao":
        registrar, encerrar = create_or_merge_position, apply_position_closure
        abertura, reforco = aporte(cenario, "100", "2"), aporte(cenario, "100", "5")
    else:
        registrar, encerrar = aportar_acao, encerrar_acao
        atributos = {"owner_id": cenario.dono.id, "broker_id": cenario.corretora.id,
                     "ticker_id": cenario.contrato.underlying_ticker_id,
                     "portfolio_id": cenario.carteira.id, "side": Side.BUY}
        abertura = Position(**atributos, quantity=Decimal("100"), average_cost=Decimal("2"))
        reforco = Position(**atributos, quantity=Decimal("100"), average_cost=Decimal("5"))
    abertura.opened_on = date(2026, 1, 2)
    reforco.opened_on = date(2026, 1, 1) if antes_abertura else date(2026, 1, 5)
    posicao, _ = registrar(abertura)
    venda = encerrar(posicao.id, Decimal("8"), date(2026, 1, 10), Decimal("50"), owner_id=cenario.dono.id)
    if instrumento == "acao" and antes_abertura:
        registrar(reforco, confirm_prior_opening=True)
    else:
        registrar(reforco)
    sessao.flush()
    sessao.expire_all()
    atual = sessao.get(type(posicao), posicao.id)
    venda = sessao.get(Transaction, venda.id)
    assert atual.quantity == Decimal("150") and atual.average_cost == Decimal("3.5")
    assert venda.average_cost == Decimal("3.5") and venda.result == Decimal("225")


@pytest.mark.parametrize("instrumento", ["acao", "opcao"])
def test_aporte_anterior_recusa_posicao_legada_sem_extrato(sessao, cenario, instrumento):
    if instrumento == "opcao":
        registrar = create_or_merge_position
        abertura, reforco = aporte(cenario, "100", "2"), aporte(cenario, "100", "5")
    else:
        registrar = aportar_acao
        atributos = {"owner_id": cenario.dono.id, "broker_id": cenario.corretora.id,
                     "ticker_id": cenario.contrato.underlying_ticker_id,
                     "portfolio_id": cenario.carteira.id, "side": Side.BUY}
        abertura = Position(**atributos, quantity=Decimal("100"), average_cost=Decimal("2"))
        reforco = Position(**atributos, quantity=Decimal("100"), average_cost=Decimal("5"))
    abertura.opened_on, reforco.opened_on = date(2026, 1, 2), date(2026, 1, 1)
    posicao, _ = registrar(abertura)
    sessao.flush()
    posicao.movements.clear()
    sessao.flush()
    with pytest.raises(ValueError, match="extrato anterior está ausente"):
        if instrumento == "acao":
            registrar(reforco, confirm_prior_opening=True)
        else:
            registrar(reforco)
    sessao.flush()
    sessao.expire_all()
    atual = sessao.get(type(posicao), posicao.id)
    assert atual.quantity == Decimal("100") and atual.average_cost == Decimal("2")
    assert not atual.movements


