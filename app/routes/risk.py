from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import render_template
from sqlalchemy import select

from app import db
from app.models import AppSetting, Ticker
from app.performance.risk import (
    MIN_OBSERVATIONS_FOR_CONFIDENCE,
    PortfolioDrawdown,
    TickerRiskMetrics,
    build_portfolio_drawdown,
    build_ticker_risk_metrics,
)
from app.positions.holdings_history import DividendEvent, HoldingEvent
from app.routes import bp
from app.routes.helpers import (
    dividend_events,
    open_real_quantities_by_ticker,
    position_movement_events,
    price_series_by_ticker,
    ticker_price_series,
)


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
    for ticker_id in quantities_by_ticker:
        ticker = tickers.get(ticker_id)
        if ticker is None:
            continue
        series = [(entry.recorded_date, entry.price) for entry in ticker_price_series(ticker_id)]
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
    #
    # A quantidade vem do extrato (`position_movement_events`), não do saldo
    # de hoje: medir o passado com a posição atual mostraria um patrimônio
    # que nunca existiu sempre que houve aumento. Sem argumentos porque esta
    # página não tem filtro de carteira nem de corretora — é a carteira real
    # inteira, e a exclusão da carteira simulada já vem de dentro.
    events = position_movement_events()
    events_by_currency: dict[str, list[HoldingEvent]] = {}
    for event in events:
        ticker = tickers.get(event.ticker_id)
        if ticker is None:
            continue
        events_by_currency.setdefault(ticker.currency, []).append(event)

    # Os proventos entram no numerador do retorno porque o app não tem conta
    # caixa: sem esse crédito o dinheiro recebido sumiria na data ex (o preço
    # cai, o patrimônio cai, e nada compensa). Aqui não se rateia
    # (`prorate_dividends`) como em Performance: sem filtro, o recorte É o
    # total e a razão seria sempre 1.
    dividends_by_currency: dict[str, list[DividendEvent]] = {}
    for dividend in dividend_events({event.ticker_id for event in events}):
        ticker = tickers.get(dividend.ticker_id)
        if ticker is not None:
            dividends_by_currency.setdefault(ticker.currency, []).append(dividend)

    # Uma consulta para todas as séries, e não uma por ticker: os eventos
    # trazem tickers que `quantities_by_ticker` não cobre (contratos de
    # opção, por exemplo).
    drawdown_series = price_series_by_ticker({event.ticker_id for event in events})

    portfolio_drawdowns: list[PortfolioDrawdown] = [
        build_portfolio_drawdown(
            currency,
            currency_events,
            drawdown_series,
            dividends_by_currency.get(currency, []),
        )
        for currency, currency_events in sorted(events_by_currency.items())
    ]

    benchmark_ticker = tickers.get(benchmark_ticker_id) if benchmark_ticker_id else None

    return render_template(
        "risk.html",
        ticker_metrics=ticker_metrics,
        portfolio_drawdowns=portfolio_drawdowns,
        benchmark_ticker=benchmark_ticker,
        min_observations=MIN_OBSERVATIONS_FOR_CONFIDENCE,
    )
