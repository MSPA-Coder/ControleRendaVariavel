"""Fragmentos HTML consumidos pelo HTMX.

Estas rotas devolvem pedaços de página, não JSON: o servidor continua sendo
quem renderiza e quem decide o que o usuário vê. Elas usam os mesmos macros
dos templates completos, então não existe uma segunda cópia da apresentação
para manter em sincronia.

Autorização é a de sempre — ``_require_login`` protege todas as rotas do
app. O cabeçalho ``HX-Request`` nunca é consultado aqui como permissão.
"""

from __future__ import annotations

from contextlib import suppress

from flask import render_template, request
from flask.typing import ResponseReturnValue

from app import limiter
from app.collector_heartbeat import collector_heartbeat
from app.routes import bp
from app.routes.helpers import quote_stale_after_seconds, rtd_service


def _render_heartbeat() -> str:
    return render_template(
        "partials/collector_heartbeat.html",
        collector_heartbeat=collector_heartbeat(
            stale_after_seconds=quote_stale_after_seconds()
        ),
    )


def _render_rtd_toggle() -> str:
    """Estado atual do coletor, tolerante a um controlador indisponível.

    O controlador RTD é um sistema externo: se ele não responde, a página
    mostra o coletor como indisponível em vez de quebrar. Nenhuma operação
    de cadastro depende dele.
    """
    service = rtd_service()
    try:
        running, available, status = service.is_running, service.available, service.status
    except (OSError, RuntimeError):
        running, available, status = False, False, "unavailable"
    return render_template(
        "partials/rtd_toggle.html",
        rtd_service_running=running,
        rtd_service_available=available,
        rtd_service_status=status,
        collector_heartbeat=collector_heartbeat(
            stale_after_seconds=quote_stale_after_seconds()
        ),
    )


@bp.get("/partials/collector-heartbeat")
@limiter.limit("120 per minute")
def collector_heartbeat_partial() -> ResponseReturnValue:
    return _render_heartbeat()


@bp.route("/partials/rtd-service", methods=["GET", "POST"])
@limiter.limit("120 per minute")
def rtd_service_partial() -> ResponseReturnValue:
    """Lê e, no POST, alterna o coletor RTD.

    A leitura é GET e a escrita é POST, com CSRF — o HTMX envia o token pelo
    ``hx-headers`` definido em ``base.html``. O corpo do POST vem do próprio
    checkbox: presente significa ligar, ausente significa desligar.
    """
    if request.method == "POST":
        service = rtd_service()
        enabled = request.form.get("enabled") is not None
        # O fragmento devolvido abaixo já mostra o estado real (e o controle
        # desabilitado), que é a forma de reportar a falha do controlador
        # externo sem derrubar a página.
        with suppress(OSError, RuntimeError):
            service.start() if enabled else service.stop()
    return _render_rtd_toggle()
