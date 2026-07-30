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
from app.models import AppSetting, CollectorMode
from app.pricing_settings import parse_pricing_settings
from app.routes import bp


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
            return render_template(
                "settings.html",
                settings=submitted,
                min_interval=MIN_POLL_INTERVAL_SECONDS,
                max_interval=MAX_POLL_INTERVAL_SECONDS,
            ), 422
        current_settings.collector_mode = data.collector_mode
        current_settings.poll_interval_seconds = data.poll_interval_seconds
        current_settings.risk_free_rate_annual = pricing_data.risk_free_rate_annual
        db.session.commit()
        flash("Configurações do coletor atualizadas.", "success")
        return redirect(url_for("portfolio.settings"))

    db.session.commit()
    return render_template(
        "settings.html",
        settings=current_settings,
        min_interval=MIN_POLL_INTERVAL_SECONDS,
        max_interval=MAX_POLL_INTERVAL_SECONDS,
    )
