from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from flask import current_app, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.authorization import requer_admin
from app.collector_settings import (
    DEFAULT_AGENT_CHECK_INTERVAL_SECONDS,
    DEFAULT_COLLECTOR_SCHEDULE_END_TIME,
    DEFAULT_COLLECTOR_SCHEDULE_START_TIME,
    DEFAULT_COLLECTOR_SCHEDULE_WEEKDAYS,
    MAX_AGENT_CHECK_INTERVAL_SECONDS,
    MAX_POLL_INTERVAL_SECONDS,
    MIN_AGENT_CHECK_INTERVAL_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    default_collector_settings,
    parse_agent_check_interval,
    parse_collector_schedule,
    parse_collector_settings,
)
from app.models import AppSetting, CollectorMode, Ticker
from app.pricing_settings import parse_pricing_settings
from app.routes import bp
from app.routes.helpers import (
    rtd_service_state,
    ticker_records,
)
from app.themes import DEFAULT_THEME, THEME_OPTIONS, parse_theme

_WEEKDAY_OPTIONS = (
    (0, "Segunda-feira"),
    (1, "Terça-feira"),
    (2, "Quarta-feira"),
    (3, "Quinta-feira"),
    (4, "Sexta-feira"),
    (5, "Sábado"),
    (6, "Domingo"),
)


def _submitted_settings() -> AppSetting:
    """Re-render an invalid submission without changing persisted settings."""
    submitted = default_collector_settings()
    raw_theme = request.form.get("theme", DEFAULT_THEME).strip().lower()
    submitted.theme = (
        raw_theme
        if raw_theme in {theme_id for theme_id, _, _ in THEME_OPTIONS}
        else DEFAULT_THEME
    )
    raw_mode = request.form.get("collector_mode", "")
    if raw_mode in {mode.value for mode in CollectorMode}:
        submitted.collector_mode = CollectorMode(raw_mode)
    try:
        submitted.poll_interval_seconds = int(request.form.get("poll_interval_seconds", "2"))
    except ValueError:
        submitted.poll_interval_seconds = 2
    try:
        submitted.agent_check_interval_seconds = int(
            request.form.get("agent_check_interval_seconds", DEFAULT_AGENT_CHECK_INTERVAL_SECONDS)
        )
    except ValueError:
        submitted.agent_check_interval_seconds = DEFAULT_AGENT_CHECK_INTERVAL_SECONDS
    submitted.collector_schedule_weekdays = ",".join(
        value
        for value in request.form.getlist("collector_schedule_weekdays")
        if value in {str(day) for day, _ in _WEEKDAY_OPTIONS}
    ) or DEFAULT_COLLECTOR_SCHEDULE_WEEKDAYS
    try:
        submitted.collector_schedule_start_time = datetime.strptime(
            request.form.get("collector_schedule_start_time", ""), "%H:%M"
        ).time()
    except ValueError:
        submitted.collector_schedule_start_time = DEFAULT_COLLECTOR_SCHEDULE_START_TIME
    try:
        submitted.collector_schedule_end_time = datetime.strptime(
            request.form.get("collector_schedule_end_time", ""), "%H:%M"
        ).time()
    except ValueError:
        submitted.collector_schedule_end_time = DEFAULT_COLLECTOR_SCHEDULE_END_TIME
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
    return submitted


def _get_or_create_settings() -> AppSetting:
    """Fetch the singleton settings row, creating it on first use."""
    settings = db.session.get(AppSetting, 1)
    if settings is None:
        settings = default_collector_settings()
        db.session.add(settings)
        db.session.flush()
    return settings


def _render_settings(settings: AppSetting, *, status: int = 200) -> ResponseReturnValue:
    running, available, rtd_status = rtd_service_state()
    return (
        render_template(
            "settings.html",
            settings=settings,
            min_interval=MIN_POLL_INTERVAL_SECONDS,
            max_interval=MAX_POLL_INTERVAL_SECONDS,
            min_agent_check_interval=MIN_AGENT_CHECK_INTERVAL_SECONDS,
            max_agent_check_interval=MAX_AGENT_CHECK_INTERVAL_SECONDS,
            weekday_options=_WEEKDAY_OPTIONS,
            selected_schedule_weekdays={
                int(value)
                for value in settings.collector_schedule_weekdays.split(",")
                if value.isdigit()
            },
            tickers=ticker_records(),
            theme_options=THEME_OPTIONS,
            rtd_service_running=running,
            rtd_service_available=available,
            rtd_service_status=rtd_status,
            remote_collector_enabled=current_app.config["REMOTE_COLLECTOR_ENABLED"],
        ),
        status,
    )


# Restrito a `admin`: esta tela altera o coletor, os parâmetros de precificação
# e o benchmark do Beta — decisões que mudam todos os números exibidos a todo
# mundo, não apenas os lançamentos de quem edita.
@bp.route("/settings", methods=["GET", "POST"])
@requer_admin
def settings() -> ResponseReturnValue:
    current_settings = _get_or_create_settings()

    if request.method == "POST":
        try:
            data = parse_collector_settings(request.form)
            pricing_data = parse_pricing_settings(request.form)
            agent_check_interval_seconds = parse_agent_check_interval(request.form)
            schedule = parse_collector_schedule(
                request.form, request.form.getlist("collector_schedule_weekdays")
            )
            theme = parse_theme(request.form)
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
            return _render_settings(_submitted_settings(), status=422)
        try:
            current_settings = _get_or_create_settings()
            current_settings.collector_mode = data.collector_mode
            current_settings.theme = theme
            current_settings.poll_interval_seconds = data.poll_interval_seconds
            current_settings.agent_check_interval_seconds = agent_check_interval_seconds
            (
                current_settings.collector_schedule_weekdays,
                current_settings.collector_schedule_start_time,
                current_settings.collector_schedule_end_time,
            ) = schedule
            current_settings.risk_free_rate_annual = pricing_data.risk_free_rate_annual
            current_settings.benchmark_ticker_id = benchmark_ticker_id
            current_settings.stale_alert_seconds = stale_alert_seconds
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Não foi possível salvar as configurações.", "error")
            return _render_settings(_submitted_settings(), status=503)
        flash("Configurações do coletor atualizadas.", "success")
        return redirect(url_for("portfolio.settings"))

    db.session.commit()
    return _render_settings(current_settings)


@bp.post("/settings/collector/refresh")
@requer_admin
def request_collector_refresh() -> ResponseReturnValue:
    """Enfileira uma leitura para o agente Windows, sem abrir porta no host."""
    if not current_app.config["REMOTE_COLLECTOR_ENABLED"]:
        flash("A atualização remota só está disponível no ambiente VPS.", "error")
        return redirect(url_for("portfolio.settings"))
    settings = _get_or_create_settings()
    settings.collector_refresh_requested_at = datetime.now(UTC)
    db.session.commit()
    flash("Atualização solicitada ao coletor Windows.", "success")
    return redirect(url_for("portfolio.settings"))
