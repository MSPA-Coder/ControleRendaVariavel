"""Rota para alternar a privacidade visual sem alterar dados financeiros."""

from __future__ import annotations

from flask import redirect, request, session, url_for
from flask.typing import ResponseReturnValue
from sharedauth.access import url_proximo_seguro

from app.privacy import VALUES_HIDDEN_SESSION_KEY
from app.routes import bp


@bp.post("/privacy/toggle-values")
def toggle_values_privacy() -> ResponseReturnValue:
    session[VALUES_HIDDEN_SESSION_KEY] = not bool(
        session.get(VALUES_HIDDEN_SESSION_KEY, False)
    )
    # Mesmo `next` de terceiro do login, mesma checagem: o valor volta pelo
    # navegador, e sem ela esta rota vira o redirecionador aberto que a do
    # login deixou de ser.
    target = url_proximo_seguro(request.form.get("next")) or url_for("portfolio.index")
    return redirect(target)

