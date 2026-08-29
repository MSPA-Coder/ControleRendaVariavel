"""Autorizacao por papel.

A autenticacao ja e garantida por `_require_login` em `app/__init__.py`: quando
um decorator daqui roda, ha sessao. O que se decide aqui e o que aquele usuario
pode fazer.

A verificacao vive no servidor. Esconder o item no template e apresentacao, nao
controle: um botao ausente nao impede ninguem de chamar a rota diretamente.
"""

from __future__ import annotations

from flask_login import current_user  # type: ignore[import-untyped]
from sharedauth.access import requer_papel

#: Restringe a rota a usuarios com papel `admin`.
#:
#: A mecanica de recusa vem de `sharedauth.access.requer_papel`, compartilhada
#: com o MegaSena: 403, nunca redirecionamento para o login -- quem chegou aqui
#: esta autenticado, e mandar para o login sugeriria que entrar de novo
#: resolveria. Quem decide o que e ser admin continua sendo este projeto.
#:
#: `getattr` com padrao `False`, e nao `current_user.is_admin` direto: o
#: usuario anonimo do Flask-Login nao tem esse atributo, e um erro futuro na
#: lista de endpoints publicos viraria AttributeError (500) em vez de 403.
requer_admin = requer_papel(
    lambda: bool(getattr(current_user, "is_admin", False)),
    mensagem="Acesso restrito a administradores.",
)
