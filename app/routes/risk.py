from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import render_template
from sqlalchemy import select

from app import db
from app.models import AppSetting, Ticker
from app.risk import (
    MIN_OBSERVATIONS_FOR_CONFIDENCE,
    PortfolioDrawdown,
    TickerRiskMetrics,
    build_portfolio_drawdown,
    build_ticker_risk_metrics,
)
from app.routes import bp
from app.routes.helpers import open_real_quantities_by_ticker, ticker_price_series


@bp.get("/risk")
def risk_report() -> str:
    app_settings = db.session.get(AppSetting, 1)
    risk_free_rate = app_settings.risk_free_rate_annual if app_settings else Decimal("0")
    benchmark_ticker_id = app_settings.benchmark_ticker_id if app_settings else None

    quantities_by_ticker = open_real_quantities_by_ticker()
    tickers = {ticker.id: ticker for ticker in db.session.scalars(select(Ticker))}

    benchmark_series: list[tuple[date, Decimal]] | None = None
    if benchmark_ticker_id is not None:
        benchmark_series = [
            (entry.recorded_date, entry.price) for entry in ticker_price_series(benchmark_ticker_id)
        ]

    ticker_metrics: list[TickerRiskMetrics] = []
    price_series_by_ticker: dict[int, list[tuple[date, Decimal]]] = {}
    for ticker_id in quantities_by_ticker:
        ticker = tickers.get(ticker_id)
        if ticker is None:
            continue
        series = [(entry.recorded_date, entry.price) for entry in ticker_price_series(ticker_id)]
        price_series_by_ticker[ticker_id] = series
        use_benchmark = (
            benchmark_series if benchmark_ticker_id and benchmark_ticker_id != ticker_id else None
        )
        ticker_metrics.append(
            build_ticker_risk_metrics(
                ticker=ticker.symbol,
                currency=ticker.currency,
                series=series,
                risk_free_rate_annual=risk_free_rate,
                benchmark_series=use_benchmark,
            )
        )
    ticker_metrics.sort(key=lambda metrics: metrics.ticker)

    # Drawdown por carteira, sempre agrupado por moeda — nunca somando
    # moedas diferentes, mesmo princípio do resto do app (ver
    # app/portfolio.py).
    quantities_by_currency: dict[str, dict[int, Decimal]] = {}
    for ticker_id, quantity in quantities_by_ticker.items():
        ticker = tickers.get(ticker_id)
        if ticker is None:
            continue
        quantities_by_currency.setdefault(ticker.currency, {})[ticker_id] = quantity

    portfolio_drawdowns: list[PortfolioDrawdown] = [
        build_portfolio_drawdown(currency, quantities, price_series_by_ticker)
        for currency, quantities in sorted(quantities_by_currency.items())
    ]

    benchmark_ticker = tickers.get(benchmark_ticker_id) if benchmark_ticker_id else None

    return render_template(
        "risk.html",
        ticker_metrics=ticker_metrics,
        portfolio_drawdowns=portfolio_drawdowns,
        benchmark_ticker=benchmark_ticker,
        min_observations=MIN_OBSERVATIONS_FOR_CONFIDENCE,
    )
