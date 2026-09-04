from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy.exc import SQLAlchemyError

from app import db, esquecer_tema_da_sessao
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
from app.models import AppSetting, CollectorDestination, CollectorMode, Ticker
from app.pricing_settings import parse_pricing_settings
from app.routes import bp
from app.routes.helpers import ticker_records
from app.themes import (
    DEFAULT_THEME,
    THEME_DESCRIPTIONS,
    THEME_OPTIONS,
    get_theme_options_dict,
    parse_theme,
)

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
    # O destino não vem deste formulário -- tem botão próprio. Reexibir o
    # padrão aqui mostraria "entregando ao VPS" para quem está coletando
    # localmente, só porque outro campo da tela ficou inválido.
    persisted = db.session.get(AppSetting, 1)
    if persisted is not None:
        submitted.collector_destination = persisted.collector_destination
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
            theme_options=get_theme_options_dict(),
            theme_descriptions=THEME_DESCRIPTIONS,
            current_theme=settings.theme,
            collector_enabled=not settings.collector_paused,
            remote_collector_enabled=current_app.config["REMOTE_COLLECTOR_ENABLED"],
            collector_destination=settings.collector_destination,
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
            # O tema fica guardado na sessão para não custar uma consulta por
            # render (ver `_theme_context`); trocá-lo aqui exige descartar o
            # valor guardado, senão a pessoa continuaria vendo o tema antigo.
            esquecer_tema_da_sessao()
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
    """Enfileira uma leitura para o agente Windows, sem abrir porta no host.

    Vale para os dois destinos: o coletor lê o pedido no mesmo campo, esteja
    ele entregando ao VPS ou gravando no banco local.
    """
    settings = _get_or_create_settings()
    settings.collector_refresh_requested_at = datetime.now(UTC)
    db.session.commit()
    flash("Atualização solicitada ao coletor Windows.", "success")
    return redirect(url_for("portfolio.settings"))


@bp.post("/settings/collector/destination")
@requer_admin
def switch_collector_destination() -> ResponseReturnValue:
    """Alterna o destino da coleta entre o VPS e o banco desta máquina.

    Recusa fora da instância local. O `REMOTE_COLLECTOR_ENABLED` já separa os
    dois deploys, e só o banco da máquina do ProfitChart é consultado pelo
    coletor -- trocar o valor no VPS não teria efeito nenhum e deixaria as
    duas linhas discordando sobre o que está acontecendo. Esconder o botão no
    template não basta: a recusa precisa estar aqui, onde o POST chega.
    """
    if current_app.config["REMOTE_COLLECTOR_ENABLED"]:
        abort(403)
    settings = _get_or_create_settings()
    settings.collector_destination = (
        CollectorDestination.LOCAL
        if settings.collector_destination is CollectorDestination.REMOTE
        else CollectorDestination.REMOTE
    )
    db.session.commit()
    if settings.collector_destination is CollectorDestination.LOCAL:
        flash("A coleta passa a gravar no banco deste computador.", "success")
    else:
        flash("A coleta volta a ser entregue ao VPS.", "success")
    return redirect(url_for("portfolio.settings"))
