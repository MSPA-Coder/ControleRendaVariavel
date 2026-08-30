"""Autorizacao por papel.

A autenticacao ja e garantida por `_require_login` em `app/__init__.py`: quando
um decorator daqui roda, ha sessao. O que se decide aqui e o que aquele usuario
pode fazer.

A verificacao vive no servidor. Esconder o item no template e apresentacao, nao
controle: um botao ausente nao impede ninguem de chamar a rota diretamente.
"""

from __future__ import annotations

from collections.abc import Callable

from flask_login import current_user  # type: ignore[import-untyped]
from sharedauth.access import requer_papel

#: Nome do papel exigido, gravado na view protegida.
#:
#: Existe para que uma varredura da URLconf consiga distinguir "protegida por
#: papel" de "embrulhada por qualquer decorator". `functools.wraps` deixa
#: `__wrapped__` em toda view decorada -- inclusive nas que so tem
#: `@login_required` --, entao procurar por `__wrapped__` responde a pergunta
#: errada. Ver `tests/test_authorization.py`.
PAPEL_ADMIN = "admin"

#: A mecanica de recusa vem de `sharedauth.access.requer_papel`, compartilhada
#: com o MegaSena: 403, nunca redirecionamento para o login -- quem chegou aqui
#: esta autenticado, e mandar para o login sugeriria que entrar de novo
#: resolveria. Quem decide o que e ser admin continua sendo este projeto.
#:
#: `getattr` com padrao `False`, e nao `current_user.is_admin` direto: o
#: usuario anonimo do Flask-Login nao tem esse atributo, e um erro futuro na
#: lista de endpoints publicos viraria AttributeError (500) em vez de 403.
_recusar_quem_nao_e_admin = requer_papel(
    lambda: bool(getattr(current_user, "is_admin", False)),
    mensagem="Acesso restrito a administradores.",
)


def requer_admin[F: Callable[..., object]](view: F) -> F:
    """Restringe a rota a usuarios com papel `admin`, marcando a view."""
    protegida = _recusar_quem_nao_e_admin(view)
    protegida.papel_exigido = PAPEL_ADMIN  # type: ignore[attr-defined]
    return protegida  # type: ignore[return-value]
