from __future__ import annotations

from sharedauth.health import registrar_health
from sqlalchemy import select

from app import db
from app.routes import bp

# A rota em si vem de `sharedauth.health`: era a mesma sonda escrita de tres
# jeitos entre os projetos -- e ausente no MegaSena, cujo health check batia na
# raiz do site e reportava "saudavel" com o banco fora.
#
# `endpoint="health"` mantem o nome `portfolio.health`, que `PUBLIC_ENDPOINTS`
# e os testes ja referenciam.
#
# Mudanca de comportamento: com o banco inalcancavel a rota responde 503
# ("erro") em vez de deixar a excecao virar 500. Para o Docker o efeito e o
# mesmo -- contêiner doente --, mas o 503 e a resposta correta para
# indisponibilidade temporaria e nao produz traceback no log a cada sonda.
registrar_health(
    bp,
    servico="controle-renda-variavel",
    verificar=lambda: db.session.execute(select(1)),
    endpoint="health",
)
