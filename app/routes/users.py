from __future__ import annotations

from flask import Blueprint, flash, render_template, request
from flask.typing import ResponseReturnValue

from app import db
from app.accounts.authorization import requer_admin
from app.accounts.users import (
    UserManagementError,
    create_user,
    list_users,
    reset_password,
    set_active,
    update_user,
)
from app.routes.helpers import is_htmx_request

bp = Blueprint("users", __name__)


def _render_users(status: int = 200, **extra: object) -> ResponseReturnValue:
    return render_template(
        "users.html", users=list_users(), valid_roles=("admin", "operador"), **extra
    ), status


def _render_results(status: int = 200, **extra: object) -> ResponseReturnValue:
    return render_template(
        "partials/users_results.html",
        users=list_users(),
        valid_roles=("admin", "operador"),
        include_toast=True,
        **extra,
    ), status


def _response(status: int = 200, **extra: object) -> ResponseReturnValue:
    """Resposta da tela de usuarios, com ou sem HTMX.

    `extra` existe para a senha temporaria, que **nao pode ir por `flash()`**:
    o `flash` do Flask guarda a mensagem na sessao, e a sessao e um cookie
    assinado -- assinado nao e cifrado. Uma senha em texto claro ali sairia
    legivel no cabecalho da resposta. Vai como variavel de contexto do
    template, que morre no HTML da propria resposta.
    """
    if is_htmx_request():
        return _render_results(status, **extra)
    return _render_users(status, **extra)


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
    """Redefine a senha de outra conta e mostra a senha temporaria gerada.

    A senha vai no contexto do template, nunca em `flash()` -- ver `_response`.
    """
    try:
        usuario, senha_temporaria = reset_password(user_id)
        return _response(senha_temporaria=senha_temporaria, senha_de=usuario.username)
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

