from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest

from app.models import OptionPosition, OptionType, Side
from app.option_portfolio import build_option_portfolio

pytestmark = [pytest.mark.critical, pytest.mark.business_rule]


def make_option_position(
    *,
    option_type: OptionType = OptionType.CALL,
    side: Side = Side.BUY,
    quantity: str = "100",
    average_cost: str = "2",
    strike: str = "100",
    underlying_price: str | None = "100",
    last_price: str | None = "10.4506",
    expiration_date: date = date(2027, 1, 4),
    opened_on: date = date(2026, 1, 4),
) -> OptionPosition:
    quote = None
    if underlying_price is not None and last_price is not None:
        quote = SimpleNamespace(
            last_price=Decimal(last_price),
            previous_close=Decimal(last_price),
            underlying_price=Decimal(underlying_price),
            source_status="online",
            observed_at=datetime(2026, 7, 27, tzinfo=UTC),
        )
    contract = SimpleNamespace(
        option_type=option_type,
        strike=Decimal(strike),
        expiration=SimpleNamespace(exercise_date=expiration_date),
    )
    return cast(
        OptionPosition,
        SimpleNamespace(
            side=side,
            quantity=Decimal(quantity),
            average_cost=Decimal(average_cost),
            target_price=None,
            opened_on=opened_on,
            result_mode="L",
            contract=contract,
            quote=quote,
        ),
    )


def test_positions_without_quote_have_no_metrics_or_greeks() -> None:
    portfolio = build_option_portfolio(
        [make_option_position(underlying_price=None, last_price=None)],
        stale_after_seconds=30,
        risk_free_rate_annual=Decimal("0.05"),
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert portfolio.positions[0].metrics is None
    assert portfolio.positions[0].greeks is None
    assert portfolio.positions[0].quote_status == "missing"
    assert portfolio.moneyness_totals == []
    assert portfolio.total_theta_daily is None


def test_positions_with_quote_get_greeks_and_moneyness() -> None:
    # Mesmo cenário de referência (Hull): S=K=100, T=1 ano, prêmio de call
    # consistente com sigma=20% a r=5% -> greeks conhecidas (ver test_greeks.py).
    portfolio = build_option_portfolio(
        [
            make_option_position(
                option_type=OptionType.CALL,
                strike="100",
                underlying_price="100",
                last_price="10.4506",
                expiration_date=date(2027, 7, 27),
            )
        ],
        stale_after_seconds=30,
        risk_free_rate_annual=Decimal("0.05"),
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    view = portfolio.positions[0]
    assert view.greeks is not None
    assert view.greeks.moneyness == "ATM"
    assert view.greeks.delta is not None
    assert float(view.greeks.delta) > 0.5  # call ATM: delta perto de 0.5-0.65


def test_moneyness_totals_aggregate_by_bucket() -> None:
    portfolio = build_option_portfolio(
        [
            make_option_position(strike="80", underlying_price="100"),  # ITM (call)
            make_option_position(strike="120", underlying_price="100"),  # OTM (call)
            make_option_position(strike="100.2", underlying_price="100"),  # ATM
        ],
        stale_after_seconds=30,
        risk_free_rate_annual=Decimal("0.05"),
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    by_bucket = {total.moneyness: total for total in portfolio.moneyness_totals}
    assert by_bucket["ITM"].count == 1
    assert by_bucket["OTM"].count == 1
    assert by_bucket["ATM"].count == 1
    assert by_bucket["ITM"].pct == Decimal("1") / Decimal("3")
    total_pct = sum((total.pct for total in portfolio.moneyness_totals), Decimal("0"))
    assert abs(total_pct - Decimal("1")) < Decimal("0.0000000001")


def test_total_theta_daily_flips_sign_between_bought_and_sold() -> None:
    bought = build_option_portfolio(
        [make_option_position(side=Side.BUY)],
        stale_after_seconds=30,
        risk_free_rate_annual=Decimal("0.05"),
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )
    sold = build_option_portfolio(
        [make_option_position(side=Side.SELL)],
        stale_after_seconds=30,
        risk_free_rate_annual=Decimal("0.05"),
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert bought.total_theta_daily is not None
    assert sold.total_theta_daily is not None
    # Quem comprou a opção perde valor com o tempo (theta negativo); quem
    # vendeu se beneficia (sinal invertido) — mesma magnitude, sinais opostos.
    assert bought.total_theta_daily == -sold.total_theta_daily
    assert bought.total_theta_daily < 0
    assert sold.total_theta_daily > 0


def test_expirations_and_gain_loss_are_still_computed() -> None:
    # Comportamento pré-existente (não deve regredir com a adição das gregas).
    portfolio = build_option_portfolio(
        [
            make_option_position(
                side=Side.BUY, average_cost="2", last_price="10.4506", underlying_price="100"
            )
        ],
        stale_after_seconds=30,
        risk_free_rate_annual=Decimal("0.05"),
        today=date(2026, 7, 27),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert len(portfolio.expirations) == 1
    assert portfolio.gain > 0
    assert portfolio.result == portfolio.gain + portfolio.loss
