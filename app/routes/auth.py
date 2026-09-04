from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import (  # type: ignore[import-untyped]
    current_user,
    login_required,
    login_user,
    logout_user,
)
from sharedauth.access import url_proximo_seguro
from sqlalchemy import select
from werkzeug.wrappers import Response

from app import db
from app.accounts.auditoria import registrar
from app.models import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login() -> ResponseReturnValue:
    if current_user.is_authenticated:
        return redirect(url_for("portfolio.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.session.scalar(select(User).where(User.username == username))
        if user is None or not user.is_active_user or not user.check_password(password):
            # O motivo fica na trilha, mas NAO na tela: a mensagem e identica
            # para "usuario nao existe" e "senha errada" de proposito, senao
            # ela vira um oraculo de quais logins existem.
            motivo = (
                "inexistente" if user is None
                else "inativo" if not user.is_active_user
                else "senha_incorreta"
            )
            registrar(
                "sessao",
                "login_recusado",
                detalhes={"username": username, "motivo": motivo},
                usuario_id=user.id if user is not None else None,
            )
            db.session.commit()
            flash("Usuário ou senha inválidos.", "error")
            return render_template("login.html", username=username), 401
        login_user(user, remember=True)
        # `usuario_id` explicito: `current_user` so passa a valer na proxima
        # requisicao, entao aqui a sessao ainda nao conhece o autor.
        registrar("sessao", "login", entidade_id=user.id, usuario_id=user.id)
        db.session.commit()
        flash("Login realizado com sucesso.", "success")
        # `request.values` cobre a query da URL e o campo do formulário. Aqui
        # o destino chega pelo `action` (ver `login.html`), mas ler dos dois
        # lados deixa a rota indiferente a essa escolha do template.
        next_url = url_proximo_seguro(request.values.get("next"))
        if next_url is not None:
            return redirect(next_url)
        return redirect(url_for("portfolio.index"))

    return render_template("login.html", username="")


@bp.post("/logout")
@login_required  # type: ignore[misc]
def logout() -> Response:
    # Antes de `logout_user`, que apaga o autor da sessao.
    registrar("sessao", "logout", entidade_id=current_user.id)
    db.session.commit()
    logout_user()
    flash("Sessão encerrada.", "success")
    return redirect(url_for("auth.login"))
