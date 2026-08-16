"""Autorizacao por papel.

A autenticacao ja e garantida por `_require_login` em `app/__init__.py`: quando
um decorator daqui roda, ha sessao. O que se decide aqui e o que aquele usuario
pode fazer.

A verificacao vive no servidor. Esconder o item no template e apresentacao, nao
controle: um botao ausente nao impede ninguem de chamar a rota diretamente.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from flask import abort
from flask_login import current_user  # type: ignore[import-untyped]


def requer_admin[F: Callable[..., object]](view: F) -> F:
    """Restringe a rota a usuarios com papel `admin`.

    Responde 403, nao redireciona para o login: quem chegou aqui esta
    autenticado, e mandar para o login sugeriria que entrar de novo resolveria.
    """

    @wraps(view)
    def wrapper(*args: object, **kwargs: object) -> object:
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return view(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
