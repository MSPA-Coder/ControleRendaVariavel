"""Oráculos externos, paridade e diferenças finitas para o modelo europeu."""

import math
from decimal import Decimal

import pytest

from app.models import OptionType
from app.options.greeks import black_scholes_price, calculate_greeks, implied_volatility


def test_preco_de_referencia_publicado_no_quantlib():
    # QuantLib/test-suite/europeanoption.cpp, testValues, primeiro caso sem dividendos.
    assert black_scholes_price(OptionType.CALL, 60, 65, 0.25, 0.08, 0.30) == pytest.approx(2.1334, abs=1e-4)


@pytest.mark.parametrize("tipo", [OptionType.CALL, OptionType.PUT])
@pytest.mark.parametrize("spot,strike,taxa", [(100, 100, 0.05), (80, 100, 0.10), (120, 100, 0.05)])
def test_volatilidade_implicita_recupera_preco_e_gregas_conferem_derivadas(tipo, spot, strike, taxa):
    vol, prazo = 0.3, 1.0
    preco = black_scholes_price(tipo, spot, strike, prazo, taxa, vol)
    resultado = calculate_greeks(option_type=tipo, underlying_price=Decimal(spot), strike=Decimal(strike),
                                market_price=Decimal(str(preco)), remaining_days=365,
                                risk_free_rate_annual=Decimal(str(taxa)))
    assert resultado.implied_volatility is not None
    assert float(resultado.implied_volatility) == pytest.approx(vol, abs=2e-5)
    h = 0.01
    cima = black_scholes_price(tipo, spot + h, strike, prazo, taxa, vol)
    baixo = black_scholes_price(tipo, spot - h, strike, prazo, taxa, vol)
    assert float(resultado.delta) == pytest.approx((cima - baixo) / (2 * h), abs=2e-5)
    assert float(resultado.gamma) == pytest.approx((cima - 2 * preco + baixo) / h ** 2, abs=2e-5)
    dv = 0.0001
    vega = (black_scholes_price(tipo, spot, strike, prazo, taxa, vol + dv)
            - black_scholes_price(tipo, spot, strike, prazo, taxa, vol - dv)) / (2 * dv * 100)
    assert float(resultado.vega) == pytest.approx(vega, abs=2e-5)
    dt = 0.0001
    theta = (black_scholes_price(tipo, spot, strike, prazo - dt, taxa, vol)
             - black_scholes_price(tipo, spot, strike, prazo + dt, taxa, vol)) / (2 * dt * 365)
    assert float(resultado.theta_daily) == pytest.approx(theta, abs=2e-5)


@pytest.mark.parametrize("spot,strike,taxa,prazo,vol", [(80, 100, 0.10, 1, 0.2),
                                                     (120, 100, -0.05, 1, 0.2),
                                                     (100, 100, 0, 0.5, 0.5)])
def test_paridade_call_put_e_inversao_do_modelo_europeu(spot, strike, taxa, prazo, vol):
    call = black_scholes_price(OptionType.CALL, spot, strike, prazo, taxa, vol)
    put = black_scholes_price(OptionType.PUT, spot, strike, prazo, taxa, vol)
    assert call - put == pytest.approx(spot - strike * math.exp(-taxa * prazo), abs=1e-10)
    for tipo, preco in [(OptionType.CALL, call), (OptionType.PUT, put)]:
        resolvida = implied_volatility(tipo, preco, spot, strike, prazo, taxa)
        assert resolvida is not None
        assert resolvida == pytest.approx(vol, abs=2e-5)


@pytest.mark.parametrize("dias,preco", [(0, "10"), (-1, "10"), (365, "0"), (365, "1000")])
def test_expiracao_ou_premio_inviavel_nao_inventam_gregas(dias, preco):
    resultado = calculate_greeks(option_type=OptionType.CALL, underlying_price=Decimal(100),
                                strike=Decimal(100), market_price=Decimal(preco), remaining_days=dias,
                                risk_free_rate_annual=Decimal("0.05"))
    assert resultado.implied_volatility is None
    assert resultado.delta is None and resultado.gamma is None
    assert resultado.theta_daily is None and resultado.vega is None
