from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import delete

from app import db
from app.core.validation import parse_finite_decimal
from app.models import QuoteHistory, Ticker
from app.quotes.history_import import (
    DailyQuote,
    QuoteHistoryImportError,
    fetch_yahoo_daily_quotes,
)
from app.routes import bp
from app.routes.helpers import (
    benchmark_candidates,
    is_htmx_request,
    quote_update_target_tickers,
    quote_update_targets,
    stock_ticker_records,
    ticker_price_series,
    upsert_quote_history,
)


def common_quote_start_date(
    history: Sequence[QuoteHistory], benchmark_history: Sequence[QuoteHistory]
) -> date | None:
    """Return the first calendar date quoted for both comparison tickers.

    Quote history is sparse (market holidays and failed imports are not
    interpolated), so comparison starts at the first exact intersection of
    the two recorded date sets. A position's ``opened_on`` is deliberately
    not used: it does not establish that either ticker had a quote that day.
    """
    common_dates = {entry.recorded_date for entry in history}.intersection(
        entry.recorded_date for entry in benchmark_history
    )
    return min(common_dates) if common_dates else None


def trim_to_common_quote_start(
    history: Sequence[QuoteHistory], benchmark_history: Sequence[QuoteHistory]
) -> tuple[list[QuoteHistory], list[QuoteHistory], date | None]:
    """Trim comparison series to their first exact common quote date."""
    start_date = common_quote_start_date(history, benchmark_history)
    if start_date is None:
        return [], [], None
    return (
        [entry for entry in history if entry.recorded_date >= start_date],
        [entry for entry in benchmark_history if entry.recorded_date >= start_date],
        start_date,
    )


def _quote_history_context(
    *,
    ticker_id: int | None,
    benchmark_id: int | None,
    management_open: bool = False,
) -> dict[str, object]:
    """Contexto da tela de Cotacoes.

    Extraido da rota porque os formularios de "Gerenciar Cotacoes" tambem o
    montam: eles respondem ao HTMX com esta mesma regiao ja atualizada, em vez
    de mandar o navegador recarregar a pagina inteira.
    """
    tickers = stock_ticker_records()
    selected_ticker: Ticker | None = None
    if ticker_id is not None:
        selected_ticker = next(
            (ticker for ticker in tickers if ticker.id == ticker_id), None
        )
    if selected_ticker is None and tickers:
        selected_ticker = tickers[0]
    history = ticker_price_series(selected_ticker.id) if selected_ticker else []

    candidates = benchmark_candidates(
        exclude_ticker_id=selected_ticker.id if selected_ticker else None
    )
    selected_benchmark: Ticker | None = None
    if benchmark_id is not None:
        selected_benchmark = next(
            (ticker for ticker in candidates if ticker.id == benchmark_id), None
        )
    benchmark_history = (
        ticker_price_series(selected_benchmark.id) if selected_benchmark else []
    )

    # No modo de comparação, o gráfico (só o gráfico — o navegador de
    # cotações abaixo continua mostrando o histórico completo) começa na
    # primeira data com cotação registrada para os dois tickers. Como as
    # séries são diárias e esparsas, o início precisa ser uma data exata da
    # interseção, e não apenas a maior data inicial de cada série.
    chart_history = history
    chart_benchmark_history = benchmark_history
    chart_comparison_start_date: date | None = None
    if selected_benchmark is not None:
        (
            chart_history,
            chart_benchmark_history,
            chart_comparison_start_date,
        ) = trim_to_common_quote_start(history, benchmark_history)

    return {
        "tickers": tickers,
        "selected_ticker": selected_ticker,
        "history": history,
        "benchmark_candidates": candidates,
        "selected_benchmark": selected_benchmark,
        "chart_history": chart_history,
        "chart_benchmark_history": chart_benchmark_history,
        "chart_comparison_start_date": (
            chart_comparison_start_date.isoformat()
            if chart_comparison_start_date is not None
            else None
        ),
        "today": date.today().isoformat(),
        "default_import_start": (date.today() - timedelta(days=59)).isoformat(),
        "default_import_end": date.today().isoformat(),
        "quote_management_open": management_open,
    }


def _int_or_none(raw: str | None) -> int | None:
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _quote_management_response(
    ticker_id: int | None, benchmark_id: int | None = None
) -> ResponseReturnValue:
    """Resposta dos formularios de "Gerenciar Cotacoes".

    No HTMX devolve a regiao ja atualizada, com o painel de gerenciamento
    aberto: recarregar a pagina inteira fechava o painel e descartava o ticker
    selecionado, o zoom e a rolagem. Sem HTMX o redirect de sempre continua
    valendo, entao a tela funciona igual com JavaScript desligado.
    """
    if ticker_id is None:
        ticker_id = _int_or_none(request.form.get("ticker_id"))
    if benchmark_id is None:
        benchmark_id = _int_or_none(request.form.get("benchmark_ticker_id"))
    if is_htmx_request():
        return render_template(
            "partials/quotes_results.html",
            **_quote_history_context(
                ticker_id=ticker_id, benchmark_id=benchmark_id, management_open=True
            ),
        )
    query: dict[str, int] = {}
    if ticker_id is not None:
        query["ticker_id"] = ticker_id
    if benchmark_id is not None:
        query["benchmark_ticker_id"] = benchmark_id
    return redirect(url_for("portfolio.quote_history", **query))


@bp.get("/quotes")
def quote_history() -> str:
    """Cotacoes: pagina inteira, ou so a regiao trocada pelo filtro.

    A mesma URL serve os dois casos, entao o filtro empurra ao historico o
    endereco real da pagina. `HX-Request` decide apenas a forma da resposta;
    a autorizacao e identica nos dois caminhos.
    """
    context = _quote_history_context(
        ticker_id=_int_or_none(request.args.get("ticker_id")),
        benchmark_id=_int_or_none(request.args.get("benchmark_ticker_id")),
    )
    if is_htmx_request():
        return render_template("partials/quotes_results.html", **context)
    return render_template("quotes.html", **context)


@bp.post("/quotes")
def create_quote_history_entry() -> ResponseReturnValue:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        ticker_id = int(raw["ticker_id"])
        recorded_date = date.fromisoformat(raw["recorded_date"])
        price = parse_finite_decimal(raw["price"], field_name="um preço")
    except (KeyError, ValueError, ArithmeticError):
        flash("Informe um ticker, uma data e um preço válidos.", "error")
        return _quote_management_response(None)
    if price <= 0:
        flash("O preço da cotação deve ser positivo.", "error")
        return _quote_management_response(ticker_id)
    if recorded_date > date.today():
        flash("A data da cotação não pode estar no futuro.", "error")
        return _quote_management_response(ticker_id)
    if db.session.get(Ticker, ticker_id) is None:
        flash("Selecione um ticker cadastrado.", "error")
        return _quote_management_response(None)
    # Meia-noite UTC do dia informado: um lançamento manual não tem um
    # horário real de observação (ao contrário do coletor RTD), então usa
    # um horário representativo determinístico só para preencher a coluna
    # NOT NULL ``recorded_at``.
    recorded_at = datetime.combine(recorded_date, time.min, tzinfo=UTC)
    upsert_quote_history([(ticker_id, price, recorded_date, recorded_at)])
    db.session.commit()
    flash("Cotação histórica registrada.", "success")
    return _quote_management_response(ticker_id)


@bp.post("/quotes/import")
def import_quote_history() -> ResponseReturnValue:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        start_date = date.fromisoformat(raw["start_date"])
        end_date = date.fromisoformat(raw["end_date"])
    except (KeyError, ValueError):
        flash("Informe um periodo valido para a atualizacao.", "error")
        return _quote_management_response(None)
    if start_date > end_date or end_date > date.today():
        flash("O periodo deve terminar hoje ou antes e possuir data inicial valida.", "error")
        return _quote_management_response(None)

    targets = quote_update_target_tickers()
    db.session.rollback()
    imported: list[tuple[int, DailyQuote]] = []
    failures: list[str] = []
    for target in targets:
        try:
            imported.extend(
                (target.id, quote)
                for quote in fetch_yahoo_daily_quotes(target, start_date, end_date)
            )
        except QuoteHistoryImportError:
            failures.append(target.symbol)

    if imported:
        with db.session.begin():
            upsert_quote_history(
                (ticker_id, quote.price, quote.recorded_date, quote.recorded_at)
                for ticker_id, quote in imported
            )
        flash(f"{len(imported)} daily quotes updated through Yahoo Finance.", "success")
    if failures:
        flash(f"No Yahoo Finance history for: {', '.join(failures)}.", "error")
    if not imported and not failures:
        flash("No registered tickers to update.", "error")
    return _quote_management_response(None)


@bp.post("/quotes/import-position-history")
def import_position_quote_history() -> ResponseReturnValue:
    """Refresh action and option history from each ticker's earliest
    open-position date, plus every comparison benchmark."""

    targets = quote_update_targets()
    db.session.rollback()

    imported: list[tuple[int, DailyQuote]] = []
    failures: list[str] = []
    for target, start_date in targets:
        try:
            quotes = fetch_yahoo_daily_quotes(target, start_date, date.today())
        except QuoteHistoryImportError:
            failures.append(target.symbol)
            continue
        imported.extend((target.id, quote) for quote in quotes)
    if imported:
        with db.session.begin():
            upsert_quote_history(
                (ticker_id, quote.price, quote.recorded_date, quote.recorded_at)
                for ticker_id, quote in imported
            )
    flash(
        f"{len(imported)} historical quotes refreshed for {len(targets) - len(failures)} tickers.",
        "success",
    )
    if failures:
        flash("No Yahoo Finance history for: " + ", ".join(failures), "error")
    return _quote_management_response(None)


@bp.post("/quotes/delete-by-date")
def delete_quote_history_by_date() -> ResponseReturnValue:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        ticker_id = int(raw["ticker_id"])
        recorded_date = date.fromisoformat(raw["recorded_date"])
    except (KeyError, ValueError):
        flash("Informe um ticker e uma data de cotacao validos.", "error")
        return _quote_management_response(None)

    deleted_ticker_id = db.session.scalar(
        delete(QuoteHistory).where(
            QuoteHistory.ticker_id == ticker_id,
            QuoteHistory.recorded_date == recorded_date,
        ).returning(QuoteHistory.ticker_id)
    )
    db.session.commit()
    if deleted_ticker_id is not None:
        flash("Cotacao historica excluida.", "success")
    else:
        flash("Nao ha cotacao registrada para a data informada.", "error")
    return _quote_management_response(ticker_id)
