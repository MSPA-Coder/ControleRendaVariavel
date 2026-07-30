from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from app.models import OptionType

DAYS_PER_YEAR = 365.0
# Faixa em torno do strike considerada "no dinheiro" (ATM). Fora dela, a
# opção é classificada como ITM ou OTM conforme o tipo (item 4, Nível
# Opções: "% ITM/ATM/OTM").
MONEYNESS_BAND = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class OptionGreeks:
    """Gregas via Black-Scholes europeu (item 4, Nível Opções).

    A volatilidade implícita é calculada a partir do prêmio de mercado
    observado (``OptionQuote.last_price``), não de uma série histórica do
    ativo-objeto — isso ainda não existe (ver Fase A: quote_history). Se o
    prêmio não permitir resolver uma volatilidade plausível (ex.: cotação
    zerada, expirada, ou fora da faixa considerada), todos os campos
    numéricos vêm como ``None`` e só ``moneyness`` fica preenchido, que não
    depende do modelo.

    Nota deliberada sobre tipos: o restante do domínio usa ``Decimal`` para
    valores financeiros exatos (ver AGENTS.md). Aqui a matemática é
    inerentemente contínua (log, exponencial, distribuição normal) e
    Black-Scholes já é, por definição, um modelo aproximado — não uma soma
    contábil. ``float`` é a escolha certa por dentro; a fronteira com
    ``Decimal`` é preservada nas entradas e nos campos deste dataclass.
    """

    implied_volatility: Decimal | None
    """Volatilidade anualizada implícita no prêmio observado (ex.: 0.35 = 35%)."""
    delta: Decimal | None
    gamma: Decimal | None
    theta_daily: Decimal | None
    """Variação esperada do prêmio por dia decorrido, mantendo tudo mais
    constante (theta anual / 365). Negativo = a opção perde valor com o
    tempo, do ponto de vista de quem está comprado."""
    vega: Decimal | None
    """Variação do prêmio para +1 ponto percentual de volatilidade."""
    moneyness: str
    """"ITM", "ATM" ou "OTM", conforme o preço do ativo-objeto vs. strike."""


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1_d2(
    spot: float, strike: float, years: float, rate: float, vol: float
) -> tuple[float, float]:
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    return d1, d2


def black_scholes_price(
    option_type: OptionType, spot: float, strike: float, years: float, rate: float, vol: float
) -> float:
    """Preço europeu de Black-Scholes. Sem dividendos (ações à vista/ETFs
    simples); não modela o efeito de proventos sobre o prêmio."""
    if years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        intrinsic = spot - strike if option_type == OptionType.CALL else strike - spot
        return max(intrinsic, 0.0)
    d1, d2 = _d1_d2(spot, strike, years, rate, vol)
    discounted_strike = strike * math.exp(-rate * years)
    if option_type == OptionType.CALL:
        return spot * _norm_cdf(d1) - discounted_strike * _norm_cdf(d2)
    return discounted_strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_volatility(
    option_type: OptionType,
    market_price: float,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    *,
    low: float = 0.001,
    high: float = 5.0,
    tolerance: float = 1e-4,
    max_iterations: int = 100,
) -> float | None:
    """Bisseção sobre o preço de Black-Scholes até bater o prêmio observado.

    Bisseção em vez de Newton-Raphson: continua estável mesmo quando vega
    está perto de zero (opções bem OTM ou bem ITM), caso em que Newton
    tende a divergir.
    """
    if years <= 0 or spot <= 0 or strike <= 0 or market_price <= 0:
        return None
    intrinsic = spot - strike if option_type == OptionType.CALL else strike - spot
    intrinsic = max(intrinsic, 0.0)
    if market_price < intrinsic - tolerance:
        return None  # prêmio abaixo do valor intrínseco: não resolvível
    price_at_low = black_scholes_price(option_type, spot, strike, years, rate, low)
    if market_price <= price_at_low:
        return low
    price_at_high = black_scholes_price(option_type, spot, strike, years, rate, high)
    if market_price >= price_at_high:
        return None  # prêmio fora da faixa de vol. considerada plausível (até 500% a.a.)

    mid = (low + high) / 2.0
    for _ in range(max_iterations):
        mid = (low + high) / 2.0
        price = black_scholes_price(option_type, spot, strike, years, rate, mid)
        if abs(price - market_price) < tolerance:
            return mid
        if price < market_price:
            low = mid
        else:
            high = mid
    return mid


def classify_moneyness(option_type: OptionType, underlying_price: Decimal, strike: Decimal) -> str:
    if strike <= 0:
        return "ATM"
    relative_diff = (underlying_price - strike) / strike
    if abs(relative_diff) <= MONEYNESS_BAND:
        return "ATM"
    in_the_money = relative_diff > 0 if option_type == OptionType.CALL else relative_diff < 0
    return "ITM" if in_the_money else "OTM"


def _to_decimal(value: float, places: str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places))


def calculate_greeks(
    *,
    option_type: OptionType,
    underlying_price: Decimal,
    strike: Decimal,
    market_price: Decimal,
    remaining_days: int,
    risk_free_rate_annual: Decimal,
) -> OptionGreeks:
    moneyness = classify_moneyness(option_type, underlying_price, strike)
    spot = float(underlying_price)
    strike_f = float(strike)
    years = remaining_days / DAYS_PER_YEAR
    rate = float(risk_free_rate_annual)

    vol = implied_volatility(option_type, float(market_price), spot, strike_f, years, rate)
    if vol is None or years <= 0:
        return OptionGreeks(None, None, None, None, None, moneyness)

    d1, d2 = _d1_d2(spot, strike_f, years, rate, vol)
    pdf_d1 = _norm_pdf(d1)
    sqrt_years = math.sqrt(years)

    delta = _norm_cdf(d1) if option_type == OptionType.CALL else _norm_cdf(d1) - 1.0
    gamma = pdf_d1 / (spot * vol * sqrt_years)
    vega = spot * pdf_d1 * sqrt_years / 100.0  # por 1 ponto percentual de vol

    discounted_strike = strike_f * math.exp(-rate * years)
    time_decay_term = -(spot * pdf_d1 * vol) / (2.0 * sqrt_years)
    if option_type == OptionType.CALL:
        theta_annual = time_decay_term - rate * discounted_strike * _norm_cdf(d2)
    else:
        theta_annual = time_decay_term + rate * discounted_strike * _norm_cdf(-d2)
    theta_daily = theta_annual / DAYS_PER_YEAR

    return OptionGreeks(
        implied_volatility=_to_decimal(vol, "0.000001"),
        delta=_to_decimal(delta, "0.000001"),
        gamma=_to_decimal(gamma, "0.00000001"),
        theta_daily=_to_decimal(theta_daily, "0.000001"),
        vega=_to_decimal(vega, "0.000001"),
        moneyness=moneyness,
    )
