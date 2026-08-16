from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import (  # type: ignore[import-untyped]
    current_user,
    login_required,
    login_user,
    logout_user,
)
from sqlalchemy import select
from werkzeug.wrappers import Response

from app import db, limiter
from app.models import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login() -> ResponseReturnValue:
    if current_user.is_authenticated:
        return redirect(url_for("portfolio.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.session.scalar(select(User).where(User.username == username))
        if user is None or not user.is_active_user or not user.check_password(password):
            flash("Usuário ou senha inválidos.", "error")
            return render_template("login.html", username=username), 401
        login_user(user, remember=True)
        flash("Login realizado com sucesso.", "success")
        next_url = request.args.get("next")
        if next_url and next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect(url_for("portfolio.index"))

    return render_template("login.html", username="")


@bp.post("/logout")
@login_required  # type: ignore[misc]
def logout() -> Response:
    logout_user()
    flash("Sessão encerrada.", "success")
    return redirect(url_for("auth.login"))
