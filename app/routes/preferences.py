"""Preferências da própria conta, sem permissão para alterar o coletor."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy.exc import SQLAlchemyError

from app import db, esquecer_formato_regional_da_sessao, esquecer_tema_da_sessao
from app.core.pricing_settings import parse_pricing_settings
from app.core.regional import (
    REGIONAL_FORMAT_EXAMPLES,
    REGIONAL_FORMAT_LABELS,
    normalize_regional_format,
)
from app.core.themes import THEME_DESCRIPTIONS, get_theme_options_dict, parse_theme
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
        theme_descriptions=THEME_DESCRIPTIONS,
        regional_options=[
            (value, label, REGIONAL_FORMAT_EXAMPLES[value])
            for value, label in REGIONAL_FORMAT_LABELS.items()
        ],
    ), status


@bp.route("/preferences", methods=["GET", "POST"])
def preferences() -> ResponseReturnValue:
    """O login global é obrigatório; o dono vem exclusivamente da sessão."""
    if request.method == "POST":
        try:
            theme = parse_theme(request.form)
            regional_format = normalize_regional_format(request.form.get("regional_format"))
            pricing = parse_pricing_settings(request.form)
            benchmark_id = parse_positive_id(request.form.get("benchmark_ticker_id"), allow_all=True)
            if benchmark_id is not None and not ticker_is_entitled(benchmark_id):
                raise ValueError("Selecione uma referência do seu histórico de investimentos.")
        except ValueError as exc:
            flash(str(exc), "error")
            return _render_preferences(request.form, 422)

        try:
            preference = user_preferences()
            preference.theme = theme
            preference.regional_format = regional_format
            preference.risk_free_rate_annual = pricing.risk_free_rate_annual
            preference.benchmark_ticker_id = benchmark_id
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Não foi possível salvar suas preferências.", "error")
            return _render_preferences(request.form, 503)
        esquecer_tema_da_sessao()
        esquecer_formato_regional_da_sessao()
        flash("Preferências atualizadas.", "success")
        return redirect(url_for("portfolio.preferences"))

    return _render_preferences(user_preferences())
