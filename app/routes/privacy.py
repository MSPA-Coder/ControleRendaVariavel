"""Rota para alternar a privacidade visual sem alterar dados financeiros."""

from __future__ import annotations

from flask import redirect, request, session, url_for
from flask.typing import ResponseReturnValue

from app.privacy import VALUES_HIDDEN_SESSION_KEY
from app.routes import bp
from app.routes.auth import _local_next_url


@bp.post("/privacy/toggle-values")
def toggle_values_privacy() -> ResponseReturnValue:
    session[VALUES_HIDDEN_SESSION_KEY] = not bool(
        session.get(VALUES_HIDDEN_SESSION_KEY, False)
    )
    target = _local_next_url(request.form.get("next")) or url_for("portfolio.index")
    return redirect(target)

