"""Preferências da própria conta, sem permissão para alterar o coletor."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy.exc import SQLAlchemyError

from app import db, esquecer_tema_da_sessao
from app.core.pricing_settings import parse_pricing_settings
from app.core.themes import get_theme_options_dict, parse_theme
from app.routes import bp
from app.routes.helpers import (
    entitled_tickers,
    parse_positive_id,
    ticker_is_entitled,
    user_preferences,
)


def _render_preferences(values: object, status: int = 200) -> ResponseReturnValue:
    return render_template(
        "preferences.html",
        values=values,
        theme_options=get_theme_options_dict(),
        tickers=entitled_tickers(include_inactive=True),
    ), status


@bp.route("/preferences", methods=["GET", "POST"])
def preferences() -> ResponseReturnValue:
    """O login global é obrigatório; o dono vem exclusivamente da sessão."""
    if request.method == "POST":
        try:
            theme = parse_theme(request.form)
            pricing = parse_pricing_settings(request.form)
            benchmark_id = parse_positive_id(request.form.get("benchmark_ticker_id"), allow_all=True)
            if benchmark_id is not None and not ticker_is_entitled(benchmark_id):
                raise ValueError("Selecione uma referência do seu histórico de investimentos.")
            raw_stale = request.form.get("stale_alert_seconds", "").strip()
            if raw_stale and (
                not raw_stale.isascii() or not raw_stale.isdecimal()
                or len(raw_stale) > 5 or not 1 <= int(raw_stale) <= 86400
            ):
                raise ValueError("O alerta de cotação deve ficar entre 1 e 86400 segundos.")
            stale = int(raw_stale) if raw_stale else None
        except ValueError as exc:
            flash(str(exc), "error")
            return _render_preferences(request.form, 422)

        try:
            preference = user_preferences()
            preference.theme = theme
            preference.risk_free_rate_annual = pricing.risk_free_rate_annual
            preference.benchmark_ticker_id = benchmark_id
            preference.stale_alert_seconds = stale
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Não foi possível salvar suas preferências.", "error")
            return _render_preferences(request.form, 503)
        esquecer_tema_da_sessao()
        flash("Preferências atualizadas.", "success")
        return redirect(url_for("portfolio.preferences"))

    return _render_preferences(user_preferences())
