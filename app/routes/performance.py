from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import render_template, request
from sqlalchemy import Select, select

from app import db
from app.models import Broker, OptionContract, OptionPosition, Position, Side, Ticker
from app.monthly_performance import (
    MonthlyPerformanceReport,
    align_benchmark_to_points,
    build_monthly_performance,
    normalize_performance_period,
)
from app.routes import bp
from app.routes.helpers import benchmark_candidates, brokers, selected_filters, ticker_price_series


@bp.get("/performance")
def monthly_performance() -> str:
    position_kind, broker, selected_kind = selected_filters()
    period = normalize_performance_period(request.args.get("period"))
    portfolio = request.args.get("portfolio", "stocks")
    if portfolio not in {"all", "stocks", "options"}:
        portfolio = "stocks"

    quantities_by_ticker: dict[int, Decimal] = {}

    def add_quantities(statement: Select[tuple[int, Decimal, Side]]) -> None:
        for ticker_id, quantity, side in db.session.execute(statement):
            direction = Decimal("1") if side == Side.BUY else Decimal("-1")
            quantities_by_ticker[ticker_id] = quantities_by_ticker.get(ticker_id, Decimal("0")) + (
                direction * quantity
            )

    if portfolio in {"all", "stocks"}:
        stock_positions = select(Position.ticker_id, Position.quantity, Position.side).join(
            Position.broker_ref
        )
        if position_kind is not None:
            stock_positions = stock_positions.where(Position.position_kind == position_kind)
        if broker:
            stock_positions = stock_positions.where(Broker.name == broker)
        add_quantities(stock_positions)

    if portfolio in {"all", "options"}:
        option_positions = (
            select(OptionContract.ticker_id, OptionPosition.quantity, OptionPosition.side)
            .join(OptionPosition.contract)
            .join(OptionPosition.broker_ref)
        )
        if position_kind is not None:
            option_positions = option_positions.where(OptionPosition.position_kind == position_kind)
        if broker:
            option_positions = option_positions.where(Broker.name == broker)
        add_quantities(option_positions)

    quantities_by_ticker = {
        ticker_id: quantity for ticker_id, quantity in quantities_by_ticker.items() if quantity != 0
    }
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
        build_monthly_performance(currency, quantities, price_series_by_ticker, period=period)
        for currency, quantities in sorted(quantities_by_currency.items())
    ]

    candidates = benchmark_candidates()
    selected_benchmark: Ticker | None = None
    raw_benchmark_id = request.args.get("benchmark_ticker_id")
    if raw_benchmark_id:
        try:
            benchmark_id = int(raw_benchmark_id)
            selected_benchmark = next(
                (ticker for ticker in candidates if ticker.id == benchmark_id), None
            )
        except ValueError:
            selected_benchmark = None
    benchmark_values_by_currency: dict[str, list[str | None]] = {}
    if selected_benchmark is not None:
        benchmark_series = [
            (entry.recorded_date, entry.price)
            for entry in ticker_price_series(selected_benchmark.id)
        ]
        benchmark_values_by_currency = {
            report.currency: [
                str(value) if value is not None else None
                for value in align_benchmark_to_points(report.points, benchmark_series)
            ]
            for report in reports
        }

    return render_template(
        "performance.html",
        reports=reports,
        brokers=brokers(),
        selected_broker=broker or "",
        selected_kind=selected_kind,
        selected_portfolio=portfolio,
        selected_period=period,
        benchmark_candidates=candidates,
        selected_benchmark=selected_benchmark,
        benchmark_values_by_currency=benchmark_values_by_currency,
    )
