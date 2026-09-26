from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.performance.dividends import build_dividend_report


def test_ticker_indicators_use_current_quote_and_open_cost_basis():
    today = date(2026, 9, 26)
    current = SimpleNamespace(
        ticker="ABCD3", ticker_id=1, currency="BRL", amount=Decimal("30"),
        kind="dividendo", payment_date=today - timedelta(days=30),
        amount_per_share=Decimal("3"), quotas=Decimal("10"),
    )
    old = SimpleNamespace(
        ticker="ABCD3", ticker_id=1, currency="BRL", amount=Decimal("10"),
        kind="jcp", payment_date=today - timedelta(days=366),
        amount_per_share=Decimal("1"), quotas=Decimal("10"),
    )

    report = build_dividend_report(
        [current, old],
        cost_basis_by_ticker={1: Decimal("100")},
        quotes_by_ticker={1: Decimal("10")},
        reference_date=today,
    )

    total = report.by_ticker[0]
    assert total.total_amount == Decimal("40")
    assert total.dividend_yield_12m == Decimal("0.3")
    assert total.yield_on_cost_current == Decimal("0.4")
