from __future__ import annotations

from contextlib import suppress
from decimal import Decimal, InvalidOperation

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy.exc import SQLAlchemyError

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
from app.routes.helpers import quote_stale_after_seconds, rtd_service, ticker_records
from app.rtd_service import OperationalProfile

_OPERATIONAL_PROFILES = {"test", "production"}


def _operational_profile() -> str | None:
    """Return the host-owned profile, without guessing when the host is offline."""
    try:
        profile = rtd_service().operational_profile
    except (OSError, RuntimeError):
        return None
    if not isinstance(profile, OperationalProfile):
        return None
    return profile.value


def _submitted_settings() -> AppSetting:
    """Re-render an invalid submission without changing persisted settings."""
    submitted = default_collector_settings()
    raw_mode = request.form.get("collector_mode", "")
    if raw_mode in {mode.value for mode in CollectorMode}:
        submitted.collector_mode = CollectorMode(raw_mode)
    try:
        submitted.poll_interval_seconds = int(request.form.get("poll_interval_seconds", "2"))
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
    return submitted


def _get_or_create_settings() -> AppSetting:
    """Fetch the singleton settings row, creating it on first use."""
    settings = db.session.get(AppSetting, 1)
    if settings is None:
        settings = default_collector_settings()
        db.session.add(settings)
        db.session.flush()
    return settings


def _render_settings(
    settings: AppSetting, *, operational_profile: str | None, status: int = 200
) -> ResponseReturnValue:
    return (
        render_template(
            "settings.html",
            settings=settings,
            operational_profile=operational_profile,
            min_interval=MIN_POLL_INTERVAL_SECONDS,
            max_interval=MAX_POLL_INTERVAL_SECONDS,
            tickers=ticker_records(),
            effective_stale_alert_seconds=quote_stale_after_seconds(),
        ),
        status,
    )


@bp.route("/settings", methods=["GET", "POST"])
def settings() -> ResponseReturnValue:
    current_settings = _get_or_create_settings()

    if request.method == "POST":
        operational_profile: str | None = None
        try:
            data = parse_collector_settings(request.form)
            pricing_data = parse_pricing_settings(request.form)
            operational_profile = request.form.get("operational_profile", "").strip()
            if operational_profile not in _OPERATIONAL_PROFILES:
                raise ValueError("Selecione um perfil operacional válido.")
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
            return _render_settings(
                _submitted_settings(),
                operational_profile=(
                    operational_profile
                    if operational_profile in _OPERATIONAL_PROFILES
                    else _operational_profile()
                ),
                status=422,
            )

        # `current_settings` (and the benchmark-ticker lookup above, when a
        # benchmark was submitted) are read-only up to this point: close the
        # transaction they opened before making the slow external call to the
        # RTD host (a PowerShell subprocess or an HTTP request, either of which
        # can take several seconds). AGENTS.md: "não mantenha transações
        # abertas durante chamadas externas lentas sem necessidade". Nothing
        # has been mutated yet, so a rollback is sufficient.
        db.session.rollback()

        try:
            service = rtd_service()
            previous_profile = service.operational_profile
            service.set_operational_profile(OperationalProfile(operational_profile))
        except (OSError, RuntimeError) as exc:
            # The profile is host-owned. Do not commit unrelated database
            # settings when the host rejected (or could not receive) the change.
            db.session.rollback()
            flash(f"Não foi possível alterar o perfil operacional: {exc}", "error")
            return _render_settings(_submitted_settings(), operational_profile=None, status=503)
        try:
            # Re-fetch after the external call: the transaction opened by the
            # earlier lookup was closed above, so `current_settings` may now be
            # a stale/expired reference.
            current_settings = _get_or_create_settings()
            current_settings.collector_mode = data.collector_mode
            current_settings.poll_interval_seconds = data.poll_interval_seconds
            current_settings.risk_free_rate_annual = pricing_data.risk_free_rate_annual
            current_settings.benchmark_ticker_id = benchmark_ticker_id
            current_settings.stale_alert_seconds = stale_alert_seconds
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            with suppress(OSError, RuntimeError):
                service.set_operational_profile(previous_profile)
            flash("Não foi possível salvar as configurações.", "error")
            return _render_settings(
                _submitted_settings(), operational_profile=_operational_profile(), status=503
            )
        flash("Configurações do coletor atualizadas.", "success")
        return redirect(url_for("portfolio.settings"))

    db.session.commit()
    return _render_settings(current_settings, operational_profile=_operational_profile())
