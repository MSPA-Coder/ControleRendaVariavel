from __future__ import annotations

from contextlib import suppress
from decimal import Decimal, InvalidOperation

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from app import db
from app.collector_settings import (
    MAX_POLL_INTERVAL_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    default_collector_settings,
    parse_collector_settings,
)
from app.models import AppSetting, CollectorMode, Ticker
from app.pricing_settings import parse_pricing_settings
from app.routes import bp
from app.routes.helpers import quote_stale_after_seconds, ticker_records


@bp.route("/settings", methods=["GET", "POST"])
def settings() -> ResponseReturnValue:
    current_settings = db.session.get(AppSetting, 1)
    if current_settings is None:
        current_settings = default_collector_settings()
        db.session.add(current_settings)
        db.session.flush()

    if request.method == "POST":
        try:
            data = parse_collector_settings(request.form)
            pricing_data = parse_pricing_settings(request.form)
            raw_benchmark_id = request.form.get("benchmark_ticker_id", "").strip()
            benchmark_ticker_id = int(raw_benchmark_id) if raw_benchmark_id else None
            if benchmark_ticker_id is not None and (
                db.session.get(Ticker, benchmark_ticker_id) is None
            ):
                raise ValueError("Selecione um ticker cadastrado como referência para o Beta.")
            raw_stale_alert = request.form.get("stale_alert_seconds", "").strip()
            if raw_stale_alert:
                try:
                    stale_alert_seconds = int(raw_stale_alert)
                except ValueError as exc:
                    raise ValueError(
                        "O alerta de cotação desatualizada deve ser um número inteiro de segundos."
                    ) from exc
                if not 1 <= stale_alert_seconds <= 86400:
                    raise ValueError(
                        "O alerta de cotação desatualizada deve ficar entre 1 e 86400 segundos."
                    )
            else:
                stale_alert_seconds = None
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            submitted = default_collector_settings()
            raw_mode = request.form.get("collector_mode", "")
            if raw_mode in {mode.value for mode in CollectorMode}:
                submitted.collector_mode = CollectorMode(raw_mode)
            try:
                submitted.poll_interval_seconds = int(
                    request.form.get("poll_interval_seconds", "2")
                )
            except ValueError:
                submitted.poll_interval_seconds = 2
            with suppress(InvalidOperation, TypeError):
                submitted.risk_free_rate_annual = Decimal(
                    request.form.get("risk_free_rate_annual", "0.1075")
                )
            with suppress(ValueError, TypeError):
                raw_id = request.form.get("benchmark_ticker_id", "").strip()
                submitted.benchmark_ticker_id = int(raw_id) if raw_id else None
            with suppress(ValueError, TypeError):
                raw_stale = request.form.get("stale_alert_seconds", "").strip()
                submitted.stale_alert_seconds = int(raw_stale) if raw_stale else None
            return render_template(
                "settings.html",
                settings=submitted,
                min_interval=MIN_POLL_INTERVAL_SECONDS,
                max_interval=MAX_POLL_INTERVAL_SECONDS,
                tickers=ticker_records(),
                effective_stale_alert_seconds=quote_stale_after_seconds(),
            ), 422
        current_settings.collector_mode = data.collector_mode
        current_settings.poll_interval_seconds = data.poll_interval_seconds
        current_settings.risk_free_rate_annual = pricing_data.risk_free_rate_annual
        current_settings.benchmark_ticker_id = benchmark_ticker_id
        current_settings.stale_alert_seconds = stale_alert_seconds
        db.session.commit()
        flash("Configurações do coletor atualizadas.", "success")
        return redirect(url_for("portfolio.settings"))

    db.session.commit()
    return render_template(
        "settings.html",
        settings=current_settings,
        min_interval=MIN_POLL_INTERVAL_SECONDS,
        max_interval=MAX_POLL_INTERVAL_SECONDS,
        tickers=ticker_records(),
        effective_stale_alert_seconds=quote_stale_after_seconds(),
    )
