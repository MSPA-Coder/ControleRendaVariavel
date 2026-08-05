from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import render_template, request
from sqlalchemy import Select, select

from app import db
from app.models import Broker, OptionContract, OptionPosition, Position, Side, Ticker
from app.monthly_performance import (
    MonthlyPerformancePoint,
    MonthlyPerformanceReport,
    align_benchmark_to_points,
    build_benchmark_shadow_series,
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
    opened_on_by_ticker: dict[int, date] = {}
    # Custo total (quantidade × custo médio atual) por ticker de AÇÃO — usado
    # só para a curva hipotética do benchmark (build_benchmark_shadow_series
    # abaixo). Não é preenchido a partir de posições de opção: a comparação
    # com benchmark é restrita a ações (payoff de opção é de outra natureza,
    # ver discussão com o usuário).
    invested_amount_by_ticker: dict[int, Decimal] = {}

    def add_quantities(statement: Select[tuple[int, Decimal, Side, date]]) -> None:
        for ticker_id, quantity, side, opened_on in db.session.execute(statement):
            direction = Decimal("1") if side == Side.BUY else Decimal("-1")
            quantities_by_ticker[ticker_id] = quantities_by_ticker.get(ticker_id, Decimal("0")) + (
                direction * quantity
            )
            earliest = opened_on_by_ticker.get(ticker_id)
            if earliest is None or opened_on < earliest:
                opened_on_by_ticker[ticker_id] = opened_on

    if portfolio in {"all", "stocks"}:
        stock_positions = select(
            Position.ticker_id,
            Position.quantity,
            Position.side,
            Position.opened_on,
            Position.average_cost,
        ).join(Position.broker_ref)
        if position_kind is not None:
            stock_positions = stock_positions.where(Position.position_kind == position_kind)
        if broker:
            stock_positions = stock_positions.where(Broker.name == broker)
        for ticker_id, quantity, side, opened_on, average_cost in db.session.execute(
            stock_positions
        ):
            direction = Decimal("1") if side == Side.BUY else Decimal("-1")
            quantities_by_ticker[ticker_id] = quantities_by_ticker.get(ticker_id, Decimal("0")) + (
                direction * quantity
            )
            earliest = opened_on_by_ticker.get(ticker_id)
            if earliest is None or opened_on < earliest:
                opened_on_by_ticker[ticker_id] = opened_on
            invested_amount_by_ticker[ticker_id] = (
                invested_amount_by_ticker.get(ticker_id, Decimal("0")) + quantity * average_cost
            )

    if portfolio in {"all", "options"}:
        option_positions = (
            select(
                OptionContract.ticker_id,
                OptionPosition.quantity,
                OptionPosition.side,
                OptionPosition.opened_on,
            )
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

    position_start_by_currency: dict[str, date] = {
        currency: min(
            opened_on_by_ticker[ticker_id]
            for ticker_id in ticker_ids
            if ticker_id in opened_on_by_ticker
        )
        for currency, ticker_ids in quantities_by_currency.items()
        if any(ticker_id in opened_on_by_ticker for ticker_id in ticker_ids)
    }

    reports: list[MonthlyPerformanceReport] = [
        build_monthly_performance(currency, quantities, price_series_by_ticker, period=period)
        for currency, quantities in sorted(quantities_by_currency.items())
    ]

    # Comparação com benchmark restrita a portfolio == "stocks": com opções
    # na mesma carteira ("all"/"options"), o valor de mercado somaria
    # grandezas de natureza muito diferente (payoff não-linear de opção vs.
    # preço de um índice), e a curva hipotética abaixo só tem como projetar
    # valor investido de ações (ver invested_amount_by_ticker).
    candidates = benchmark_candidates() if portfolio == "stocks" else []
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

    # No modo de comparação, o gráfico (só o gráfico — a tabela "Dados"
    # abaixo continua mostrando o histórico completo simulado) fica
    # restrito a "desde que a posição mais antiga da moeda foi aberta":
    # comparar contra um índice usando meses anteriores à existência real
    # da carteira não diz nada sobre o desempenho dela (ver discussão com
    # o usuário / práticas de mercado, ex. Sharesight "since first
    # purchase").
    chart_points_by_currency: dict[str, list[MonthlyPerformancePoint]] = {
        report.currency: report.points for report in reports
    }
    benchmark_values_by_currency: dict[str, list[str | None]] = {}
    if selected_benchmark is not None:
        benchmark_series = [
            (entry.recorded_date, entry.price)
            for entry in ticker_price_series(selected_benchmark.id)
        ]
        for report in reports:
            currency_ticker_ids = quantities_by_currency.get(report.currency, {})
            contributions = [
                (
                    opened_on_by_ticker[ticker_id],
                    invested_amount_by_ticker.get(ticker_id, Decimal("0")),
                )
                for ticker_id in currency_ticker_ids
                if ticker_id in opened_on_by_ticker
            ]
            shadow_series = build_benchmark_shadow_series(contributions, benchmark_series)
            aligned = align_benchmark_to_points(report.points, shadow_series)
            points = report.points
            start = position_start_by_currency.get(report.currency)
            if start is not None:
                start_month = start.replace(day=1)
                kept = [
                    (point, value)
                    for point, value in zip(points, aligned, strict=True)
                    if point.month >= start_month
                ]
                points = [point for point, _ in kept]
                aligned = [value for _, value in kept]
            chart_points_by_currency[report.currency] = points
            benchmark_values_by_currency[report.currency] = [
                str(value) if value is not None else None for value in aligned
            ]

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
        chart_points_by_currency=chart_points_by_currency,
        benchmark_values_by_currency=benchmark_values_by_currency,
    )
