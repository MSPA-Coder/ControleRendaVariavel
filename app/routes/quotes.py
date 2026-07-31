from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy.dialects.postgresql import insert

from app import db
from app.models import QuoteHistory, Ticker
from app.routes import bp
from app.routes.helpers import ticker_price_series, ticker_records


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
    )


@bp.post("/quotes")
def create_quote_history_entry() -> ResponseReturnValue:
    raw = {key: value.strip() for key, value in request.form.items()}
    try:
        ticker_id = int(raw["ticker_id"])
        recorded_date = date.fromisoformat(raw["recorded_date"])
        price = Decimal(raw["price"])
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


@bp.post("/quotes/<int:entry_id>/delete")
def delete_quote_history_entry(entry_id: int) -> ResponseReturnValue:
    entry = db.get_or_404(QuoteHistory, entry_id)
    ticker_id = entry.ticker_id
    db.session.delete(entry)
    db.session.commit()
    flash("Cotação histórica excluída.", "success")
    return redirect(url_for("portfolio.quote_history", ticker_id=ticker_id))
