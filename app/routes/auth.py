from __future__ import annotations

from urllib.parse import unquote, urlsplit

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

from app import db
from app.auditoria import registrar
from app.models import User

bp = Blueprint("auth", __name__)


def _local_next_url(value: str | None) -> str | None:
    """Aceita somente um caminho absoluto interno, nunca uma URL de rede."""
    if not value or not value.startswith("/"):
        return None
    decoded = value
    # Cada unquote que altera a string encurta ao menos uma sequência ``%xx``;
    # o limite pelo tamanho original termina mesmo sob aninhamento adversarial.
    for _ in range(len(value) + 1):
        if "\\" in decoded or decoded.startswith("//"):
            return None
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    else:
        return None
    parsed = urlsplit(decoded)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return None
    return value


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
        next_url = _local_next_url(request.args.get("next"))
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
