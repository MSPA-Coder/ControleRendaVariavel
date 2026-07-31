from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import render_template
from sqlalchemy import select

from app import db
from app.models import Ticker
from app.monthly_performance import MonthlyPerformanceReport, build_monthly_performance
from app.routes import bp
from app.routes.helpers import open_real_quantities_by_ticker, ticker_price_series


@bp.get("/performance")
def monthly_performance() -> str:
    quantities_by_ticker = open_real_quantities_by_ticker()
    tickers = {ticker.id: ticker for ticker in db.session.scalars(select(Ticker))}

    price_series_by_ticker: dict[int, list[tuple[date, Decimal]]] = {
        ticker_id: [(entry.recorded_date, entry.price) for entry in ticker_price_series(ticker_id)]
        for ticker_id in quantities_by_ticker
    }

    # Mesmo princípio do resto do app: nunca somar moedas diferentes (ver
    # app/portfolio.py e app/routes/risk.py).
    quantities_by_currency: dict[str, dict[int, Decimal]] = {}
    for ticker_id, quantity in quantities_by_ticker.items():
        ticker = tickers.get(ticker_id)
        if ticker is None:
            continue
        quantities_by_currency.setdefault(ticker.currency, {})[ticker_id] = quantity

    reports: list[MonthlyPerformanceReport] = [
        build_monthly_performance(currency, quantities, price_series_by_ticker)
        for currency, quantities in sorted(quantities_by_currency.items())
    ]

    return render_template("performance.html", reports=reports)
