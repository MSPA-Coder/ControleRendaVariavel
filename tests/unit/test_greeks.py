from decimal import Decimal

import pytest

from app.greeks import (
    black_scholes_price,
    calculate_greeks,
    classify_moneyness,
    implied_volatility,
)
from app.models import OptionType

pytestmark = [pytest.mark.critical]

# Valores de referência do exemplo clássico de Hull ("Options, Futures, and
# Other Derivatives"): S=K=100, r=5%, sigma=20%, T=1 ano.
HULL_SPOT = 100.0
HULL_STRIKE = 100.0
HULL_YEARS = 1.0
HULL_RATE = 0.05
HULL_VOL = 0.20


def test_call_price_matches_published_reference_value() -> None:
    price = black_scholes_price(
        OptionType.CALL, HULL_SPOT, HULL_STRIKE, HULL_YEARS, HULL_RATE, HULL_VOL
    )
    assert price == pytest.approx(10.4506, abs=0.001)


def test_put_price_matches_published_reference_value() -> None:
    price = black_scholes_price(
        OptionType.PUT, HULL_SPOT, HULL_STRIKE, HULL_YEARS, HULL_RATE, HULL_VOL
    )
    assert price == pytest.approx(5.5735, abs=0.001)


def test_put_call_parity_holds() -> None:
    # C - P = S - K*e^(-rT); esta identidade não depende da fórmula
    # específica usada, então é uma checagem independente da implementação.
    import math

    call = black_scholes_price(
        OptionType.CALL, HULL_SPOT, HULL_STRIKE, HULL_YEARS, HULL_RATE, HULL_VOL
    )
    put = black_scholes_price(
        OptionType.PUT, HULL_SPOT, HULL_STRIKE, HULL_YEARS, HULL_RATE, HULL_VOL
    )
    expected = HULL_SPOT - HULL_STRIKE * math.exp(-HULL_RATE * HULL_YEARS)
    assert (call - put) == pytest.approx(expected, abs=1e-6)


def test_implied_volatility_round_trips_from_known_price() -> None:
    price = black_scholes_price(
        OptionType.CALL, HULL_SPOT, HULL_STRIKE, HULL_YEARS, HULL_RATE, HULL_VOL
    )
    recovered_vol = implied_volatility(
        OptionType.CALL, price, HULL_SPOT, HULL_STRIKE, HULL_YEARS, HULL_RATE
    )
    assert recovered_vol == pytest.approx(HULL_VOL, abs=1e-3)


def test_implied_volatility_returns_none_below_intrinsic_value() -> None:
    # Prêmio de R$1 para uma call com S=100, K=50 (intrínseco=50): impossível.
    result = implied_volatility(OptionType.CALL, 1.0, 100.0, 50.0, 0.5, 0.05)
    assert result is None


def test_calculate_greeks_matches_reference_deltas_and_gamma() -> None:
    call_price = Decimal(str(round(10.4506, 4)))
    greeks = calculate_greeks(
        option_type=OptionType.CALL,
        underlying_price=Decimal("100"),
        strike=Decimal("100"),
        market_price=call_price,
        remaining_days=365,
        risk_free_rate_annual=Decimal("0.05"),
    )
    assert greeks.delta is not None
    assert float(greeks.delta) == pytest.approx(0.6368, abs=0.005)
    assert greeks.gamma is not None
    assert float(greeks.gamma) == pytest.approx(0.0188, abs=0.001)
    assert greeks.theta_daily is not None
    assert float(greeks.theta_daily) < 0  # opção comprada perde valor com o tempo
    assert greeks.implied_volatility is not None
    assert float(greeks.implied_volatility) == pytest.approx(HULL_VOL, abs=0.005)


def test_calculate_greeks_put_delta_is_negative() -> None:
    put_price = Decimal(str(round(5.5735, 4)))
    greeks = calculate_greeks(
        option_type=OptionType.PUT,
        underlying_price=Decimal("100"),
        strike=Decimal("100"),
        market_price=put_price,
        remaining_days=365,
        risk_free_rate_annual=Decimal("0.05"),
    )
    assert greeks.delta is not None
    assert float(greeks.delta) == pytest.approx(-0.3632, abs=0.005)


def test_calculate_greeks_handles_expired_option_gracefully() -> None:
    greeks = calculate_greeks(
        option_type=OptionType.CALL,
        underlying_price=Decimal("100"),
        strike=Decimal("90"),
        market_price=Decimal("10"),
        remaining_days=0,
        risk_free_rate_annual=Decimal("0.05"),
    )
    assert greeks.delta is None
    assert greeks.gamma is None
    assert greeks.theta_daily is None
    assert greeks.vega is None
    assert greeks.moneyness == "ITM"  # moneyness independe do modelo


@pytest.mark.parametrize(
    ("option_type", "underlying", "strike", "expected"),
    [
        (OptionType.CALL, "110", "100", "ITM"),
        (OptionType.CALL, "90", "100", "OTM"),
        (OptionType.CALL, "100.5", "100", "ATM"),
        (OptionType.PUT, "90", "100", "ITM"),
        (OptionType.PUT, "110", "100", "OTM"),
        (OptionType.PUT, "99.5", "100", "ATM"),
    ],
)
def test_classify_moneyness(
    option_type: OptionType, underlying: str, strike: str, expected: str
) -> None:
    result = classify_moneyness(option_type, Decimal(underlying), Decimal(strike))
    assert result == expected
