from __future__ import annotations

from flask import render_template, request
from sqlalchemy import select

from app import db
from app.holdings_history import (
    DividendEvent,
    HoldingEvent,
    QuantityTimeline,
    prorate_dividends,
)
from app.models import IncomeKind, Ticker
from app.monthly_performance import (
    MonthlyPerformancePoint,
    MonthlyPerformanceReport,
    align_benchmark_to_points,
    build_benchmark_shadow_series,
    build_monthly_performance,
    normalize_performance_period,
)
from app.routes import bp
from app.routes.helpers import (
    benchmark_candidates,
    brokers,
    dividend_events,
    is_htmx_request,
    position_movement_events,
    price_series_by_ticker,
    real_portfolio_records,
    selected_filters,
    ticker_price_series,
)


@bp.get("/performance")
def monthly_performance() -> str:
    """Performance mensal: pagina inteira, ou so a regiao trocada pelo filtro.

    A mesma URL serve os dois casos, entao o filtro empurra ao historico o
    endereco real da pagina. `HX-Request` decide apenas a forma da resposta;
    a autorizacao e identica nos dois caminhos.

    A serie vem do EXTRATO das posicoes (`position_movement_events`), nao do
    saldo de hoje: a quantidade de cada data e a que a posicao realmente
    tinha naquela data, e o aporte que a aumentou e neutralizado no retorno
    em vez de aparecer como desempenho. Ver "Performance mensal" em
    `docs/planilha-acoes.md`.
    """
    portfolio_id, broker, selected_portfolio_id = selected_filters()
    period = normalize_performance_period(request.args.get("period"))
    portfolio = request.args.get("portfolio", "stocks")
    if portfolio not in {"all", "stocks", "options"}:
        portfolio = "stocks"

    # Performance continua excluindo a carteira Simulada incondicionalmente
    # qualquer que seja o filtro de Carteira escolhido — a
    # garantia vem de dentro de `position_movement_events`. Ver
    # `real_portfolio_records`, que tambem restringe as opcoes do seletor a
    # carteiras reais.
    events = position_movement_events(portfolio_id, broker)
    # `position_movement_events` devolve acoes e opcoes sempre; o filtro de
    # instrumento e aplicado sobre a origem gravada em `position_key`.
    if portfolio != "all":
        wanted = "stock" if portfolio == "stocks" else "option"
        events = [event for event in events if event.position_key[0] == wanted]

    tickers = {ticker.id: ticker for ticker in db.session.scalars(select(Ticker))}

    # Mesmo principio do resto do app: nunca somar moedas diferentes (ver
    # app/portfolio.py e app/routes/risk.py).
    events_by_currency: dict[str, list[HoldingEvent]] = {}
    for event in events:
        ticker = tickers.get(event.ticker_id)
        if ticker is None:
            continue
        events_by_currency.setdefault(ticker.currency, []).append(event)

    series_by_ticker = price_series_by_ticker({event.ticker_id for event in events})

    # Proventos entram no numerador do retorno (o app nao tem conta caixa:
    # sem esse credito o dinheiro recebido sumiria na data ex). So fazem
    # sentido com acoes no recorte — opcao nao paga provento.
    #
    # O rateio existe porque `Dividend` nao tem `portfolio_id`, so
    # `broker_id` e `ticker_id`: sem ele, filtrar por uma corretora que
    # detem metade das acoes creditaria o provento inteiro. O denominador e
    # a carteira real inteira, e por isso e a unica leitura extra — feita
    # UMA vez, fora do laco de moedas. Sem filtro de carteira/corretora o
    # recorte ja E o total, e a consulta e dispensada.
    dividends_by_currency: dict[str, list[DividendEvent]] = {}
    if portfolio in {"all", "stocks"}:
        total_events = (
            position_movement_events()
            if portfolio_id is not None or broker
            else events
        )
        total_timeline = QuantityTimeline(total_events)
        for currency, currency_events in events_by_currency.items():
            raw_dividends = dividend_events({event.ticker_id for event in currency_events})
            dividends_by_currency[currency] = prorate_dividends(
                raw_dividends, QuantityTimeline(currency_events), total_timeline
            )

    reports: list[MonthlyPerformanceReport] = [
        build_monthly_performance(
            currency,
            currency_events,
            series_by_ticker,
            dividends_by_currency.get(currency, []),
            period,
        )
        for currency, currency_events in sorted(events_by_currency.items())
    ]

    # Comparação com benchmark restrita a portfolio == "stocks": com opções
    # na mesma carteira ("all"/"options"), o valor de mercado somaria
    # grandezas de natureza muito diferente (payoff não-linear de opção vs.
    # preço de um índice).
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
    # abaixo continua mostrando o histórico completo) fica restrito a
    # "desde que a posição mais antiga da moeda foi aberta": comparar contra
    # um índice usando meses anteriores à existência real da carteira não
    # diz nada sobre o desempenho dela (mesma convenção de mercado do
    # "since first purchase").
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
            currency_events = events_by_currency.get(report.currency, [])
            # Os mesmos fluxos que o TWR neutraliza, ja avaliados a preco de
            # mercado da data (ver `portfolio_flow_series`): o comparador
            # tem de "comprar" no benchmark exatamente o valor que entrou na
            # carteira, senao as duas curvas deixam de ser comparaveis.
            shadow_series = build_benchmark_shadow_series(
                report.daily_flows, benchmark_series
            )
            aligned = align_benchmark_to_points(report.points, shadow_series)
            points = report.points
            start = min((event.occurred_on for event in currency_events), default=None)
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

    context = {
        "reports": reports,
        "brokers": brokers(),
        "selected_broker": broker or "",
        "selected_portfolio_id": selected_portfolio_id,
        "portfolios": real_portfolio_records(),
        "selected_portfolio": portfolio,
        "selected_period": period,
        "benchmark_candidates": candidates,
        "selected_benchmark": selected_benchmark,
        "chart_points_by_currency": chart_points_by_currency,
        "benchmark_values_by_currency": benchmark_values_by_currency,
        # Colunas fixas, uma por renda, mesmo nos meses sem nenhuma: coluna
        # que aparece e some conforme o mes tornaria a tabela ilegivel.
        "income_kinds": list(IncomeKind),
    }
    if is_htmx_request():
        return render_template("partials/performance_results.html", **context)
    return render_template("performance.html", **context)
