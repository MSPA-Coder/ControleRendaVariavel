from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app import db
from app.models import Position, QuoteHistory, Ticker
from app.quote_history_import import (
    DailyQuote,
    QuoteHistoryImportError,
    TickerImportTarget,
    fetch_yahoo_daily_quotes,
)
from app.routes import bp
from app.routes.helpers import ticker_price_series, ticker_records
from app.validation import parse_finite_decimal


@bp.get("/quotes")
def quote_history() -> str:
    tickers = ticker_records()
    selected_ticker: Ticker | None = None
    raw_ticker_id = request.args.get("ticker_id")
    if raw_ticker_id:
        try:
            selected_ticker = db.session.get(Ticker, int(raw_ticker_id))
        except ValueError:
            selected_ticker = None
    if selected_ticker is None and tickers:
        selected_ticker = tickers[0]
    history = ticker_price_series(selected_ticker.id) if selected_ticker else []
    return render_template(
        "quotes.html",
        tickers=tickers,
        selected_ticker=selected_ticker,
        history=history,
        today=date.today().isoformat(),
        default_import_start=(date.today() - timedelta(days=59)).isoformat(),
        default_import_end=date.today().isoformat(),
    )


@bp.post("/quotes")
def create_quote_history_entry() -> ResponseReturnValue:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        ticker_id = int(raw["ticker_id"])
        recorded_date = date.fromisoformat(raw["recorded_date"])
        price = parse_finite_decimal(raw["price"], field_name="um preço")
    except (KeyError, ValueError, ArithmeticError):
        flash("Informe um ticker, uma data e um preço válidos.", "error")
        return redirect(url_for("portfolio.quote_history"))
    if db.session.get(Ticker, ticker_id) is None:
        flash("Selecione um ticker cadastrado.", "error")
        return redirect(url_for("portfolio.quote_history"))
    if price < 0:
        flash("O preço não pode ser negativo.", "error")
        return redirect(url_for("portfolio.quote_history", ticker_id=ticker_id))
    # Meia-noite UTC do dia informado: um lançamento manual não tem um
    # horário real de observação (ao contrário do coletor RTD), então usa
    # um horário representativo determinístico só para preencher a coluna
    # NOT NULL ``recorded_at``.
    recorded_at = datetime.combine(recorded_date, time.min, tzinfo=UTC)
    # Mesmo upsert por (ticker, dia) usado pelo coletor RTD em app/cli.py:
    # um segundo lançamento manual no mesmo dia substitui o anterior em
    # vez de duplicar (a unique constraint em quote_history também impede
    # a duplicata).
    statement = insert(QuoteHistory).values(
        ticker_id=ticker_id,
        price=price,
        recorded_date=recorded_date,
        recorded_at=recorded_at,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[QuoteHistory.ticker_id, QuoteHistory.recorded_date],
        set_={
            "price": statement.excluded.price,
            "recorded_at": statement.excluded.recorded_at,
        },
    )
    db.session.execute(statement)
    db.session.commit()
    flash("Cotação histórica registrada.", "success")
    return redirect(url_for("portfolio.quote_history", ticker_id=ticker_id))


@bp.post("/quotes/import")
def import_quote_history() -> ResponseReturnValue:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        start_date = date.fromisoformat(raw["start_date"])
        end_date = date.fromisoformat(raw["end_date"])
    except (KeyError, ValueError):
        flash("Informe um periodo valido para a atualizacao.", "error")
        return redirect(url_for("portfolio.quote_history"))
    if start_date > end_date or end_date > date.today():
        flash("O periodo deve terminar hoje ou antes e possuir data inicial valida.", "error")
        return redirect(url_for("portfolio.quote_history"))

    targets = [
        TickerImportTarget(ticker.id, ticker.symbol, ticker.market)
        for ticker in ticker_records()
    ]
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
            for ticker_id, quote in imported:
                statement = insert(QuoteHistory).values(
                    ticker_id=ticker_id,
                    price=quote.price,
                    recorded_date=quote.recorded_date,
                    recorded_at=quote.recorded_at,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[QuoteHistory.ticker_id, QuoteHistory.recorded_date],
                    set_={
                        "price": statement.excluded.price,
                        "recorded_at": statement.excluded.recorded_at,
                    },
                )
                db.session.execute(statement)
        flash(f"{len(imported)} daily quotes updated through Yahoo Finance.", "success")
    if failures:
        flash(f"No Yahoo Finance history for: {', '.join(failures)}.", "error")
    if not imported and not failures:
        flash("No registered tickers to update.", "error")
    return redirect(url_for("portfolio.quote_history"))


@bp.post("/quotes/import-position-history")
def import_position_quote_history() -> ResponseReturnValue:
    """Refresh stock history from the earliest open-position date per ticker."""

    rows = db.session.execute(
        select(
            Position.ticker_id,
            Ticker.symbol,
            Ticker.market,
            func.min(Position.opened_on).label("start_date"),
        )
        .join(Ticker, Ticker.id == Position.ticker_id)
        .group_by(Position.ticker_id, Ticker.symbol, Ticker.market)
        .order_by(Ticker.symbol)
    ).all()
    targets = [
        (TickerImportTarget(row.ticker_id, row.symbol, row.market), row.start_date)
        for row in rows
    ]
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
            for ticker_id, quote in imported:
                statement = insert(QuoteHistory).values(
                    ticker_id=ticker_id,
                    price=quote.price,
                    recorded_date=quote.recorded_date,
                    recorded_at=quote.recorded_at,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[QuoteHistory.ticker_id, QuoteHistory.recorded_date],
                    set_={
                        "price": statement.excluded.price,
                        "recorded_at": statement.excluded.recorded_at,
                    },
                )
                db.session.execute(statement)
    flash(
        f"{len(imported)} historical quotes refreshed for {len(targets) - len(failures)} tickers.",
        "success",
    )
    if failures:
        flash("No Yahoo Finance history for: " + ", ".join(failures), "error")
    return redirect(url_for("portfolio.quote_history"))


@bp.post("/quotes/<int:entry_id>/delete")
def delete_quote_history_entry(entry_id: int) -> ResponseReturnValue:
    entry = db.get_or_404(QuoteHistory, entry_id)
    ticker_id = entry.ticker_id
    db.session.delete(entry)
    db.session.commit()
    flash("Cotação histórica excluída.", "success")
    return redirect(url_for("portfolio.quote_history", ticker_id=ticker_id))
