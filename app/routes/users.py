from __future__ import annotations

from flask import Blueprint, flash, render_template, request
from flask.typing import ResponseReturnValue

from app import db
from app.authorization import requer_admin
from app.routes.helpers import is_htmx_request
from app.user_management import (
    UserManagementError,
    create_user,
    list_users,
    reset_password,
    set_active,
    update_user,
)

bp = Blueprint("users", __name__)


def _render_users(status: int = 200) -> ResponseReturnValue:
    return render_template(
        "users.html", users=list_users(), valid_roles=("admin", "operador")
    ), status


def _render_results(status: int = 200) -> ResponseReturnValue:
    return render_template(
        "partials/users_results.html",
        users=list_users(),
        valid_roles=("admin", "operador"),
        include_toast=True,
    ), status


def _response(status: int = 200) -> ResponseReturnValue:
    if is_htmx_request():
        return _render_results(status)
    return _render_users(status)


@bp.get("/users")
@requer_admin
def index() -> ResponseReturnValue:
    return _render_users()


@bp.post("/users")
@requer_admin
def create() -> ResponseReturnValue:
    try:
        create_user(
            request.form.get("username", ""),
            request.form.get("role", ""),
            request.form.get("password", ""),
            request.form.get("password_confirmation", ""),
        )
        flash("Usuário criado.", "success")
        return _response()
    except UserManagementError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return _response(422)


@bp.post("/users/<int:user_id>/edit")
@requer_admin
def edit(user_id: int) -> ResponseReturnValue:
    try:
        update_user(user_id, request.form.get("username", ""), request.form.get("role", ""))
        flash("Usuário atualizado.", "success")
        return _response()
    except UserManagementError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return _response(422)


@bp.post("/users/<int:user_id>/reset-password")
@requer_admin
def reset_user_password(user_id: int) -> ResponseReturnValue:
    try:
        reset_password(
            user_id,
            request.form.get("password", ""),
            request.form.get("password_confirmation", ""),
        )
        flash("Senha redefinida.", "success")
        return _response()
    except UserManagementError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return _response(422)


@bp.post("/users/<int:user_id>/active")
@requer_admin
def change_active(user_id: int) -> ResponseReturnValue:
    try:
        active = request.form.get("active") == "1"
        set_active(user_id, active)
        flash("Usuário ativado." if active else "Usuário desativado.", "success")
        return _response()
    except UserManagementError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return _response(422)

