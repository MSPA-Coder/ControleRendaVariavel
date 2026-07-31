from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

from app.dividend_report import build_dividend_report
from app.models import Dividend


def make_dividend(
    ticker_id: int, symbol: str, currency: str, amount: str, payment_date: date
) -> Dividend:
    ticker_ref = SimpleNamespace(symbol=symbol, currency=currency)
    return cast(
        Dividend,
        SimpleNamespace(
            ticker_id=ticker_id,
            ticker_ref=ticker_ref,
            ticker=symbol,
            currency=currency,
            amount=Decimal(amount),
            payment_date=payment_date,
        ),
    )


def test_by_ticker_computes_yield_on_cost_when_cost_basis_available() -> None:
    dividends = [
        make_dividend(1, "PETR4", "BRL", "50", date(2026, 1, 15)),
        make_dividend(1, "PETR4", "BRL", "30", date(2026, 2, 15)),
    ]

    report = build_dividend_report(dividends, {1: Decimal("1000")})

    assert len(report.by_ticker) == 1
    total = report.by_ticker[0]
    assert total.ticker == "PETR4"
    assert total.total_amount == Decimal("80")
    assert total.cost_basis == Decimal("1000")
    assert total.yield_on_cost == Decimal("80") / Decimal("1000")


def test_by_ticker_yield_on_cost_is_none_without_open_position() -> None:
    dividends = [make_dividend(2, "VALE3", "BRL", "10", date(2026, 1, 15))]

    report = build_dividend_report(dividends, {})

    total = report.by_ticker[0]
    assert total.cost_basis is None
    assert total.yield_on_cost is None


def test_by_month_aggregates_across_tickers_within_same_currency() -> None:
    dividends = [
        make_dividend(1, "PETR4", "BRL", "50", date(2026, 1, 5)),
        make_dividend(2, "VALE3", "BRL", "20", date(2026, 1, 28)),
        make_dividend(1, "PETR4", "BRL", "30", date(2026, 2, 1)),
    ]

    report = build_dividend_report(dividends, {})

    assert [(total.month, total.total_amount) for total in report.by_month] == [
        (date(2026, 1, 1), Decimal("70")),
        (date(2026, 2, 1), Decimal("30")),
    ]


def test_report_is_empty_for_no_dividends() -> None:
    report = build_dividend_report([], {})

    assert report.by_ticker == []
    assert report.by_month == []
