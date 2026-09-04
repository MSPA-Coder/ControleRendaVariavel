"""Fragmentos HTML consumidos pelo HTMX.

Estas rotas devolvem pedaços de página, não JSON: o servidor continua sendo
quem renderiza e quem decide o que o usuário vê. Elas usam os mesmos macros
dos templates completos, então não existe uma segunda cópia da apresentação
para manter em sincronia.

Autorização é a de sempre — ``_require_login`` protege todas as rotas do
app. O cabeçalho ``HX-Request`` nunca é consultado aqui como permissão.
"""

from __future__ import annotations

from flask import render_template, request
from flask.typing import ResponseReturnValue

from app import db
from app.authorization import requer_admin
from app.collector_database import collector_settings_row
from app.collector_heartbeat import collector_heartbeat
from app.routes import bp
from app.routes.helpers import collector_is_enabled, quote_stale_after_seconds


def _render_heartbeat() -> str:
    return render_template(
        "partials/collector_heartbeat.html",
        collector_heartbeat=collector_heartbeat(
            stale_after_seconds=quote_stale_after_seconds()
        ),
    )


def _render_rtd_toggle() -> str:
    return render_template(
        "partials/rtd_toggle.html",
        collector_enabled=collector_is_enabled(),
        collector_heartbeat=collector_heartbeat(
            stale_after_seconds=quote_stale_after_seconds()
        ),
    )


@bp.get("/partials/collector-heartbeat")
def collector_heartbeat_partial() -> ResponseReturnValue:
    return _render_heartbeat()


@bp.route("/partials/rtd-service", methods=["GET", "POST"])
@requer_admin
def rtd_service_partial() -> ResponseReturnValue:
    """Lê e, no POST, pausa ou retoma a coleta.

    A leitura é GET e a escrita é POST, com CSRF — o HTMX envia o token pelo
    ``hx-headers`` definido em ``base.html``. O corpo do POST vem do próprio
    checkbox: presente significa coletar, ausente significa pausar.

    A pausa vale para a coleta que esta instância dirige. Na máquina do
    ProfitChart isso é a coleta com destino local; no VPS, a que o agente
    entrega por HTTPS. Em nenhum dos casos há processo sendo iniciado ou
    encerrado daqui.
    """
    if request.method == "POST":
        settings = collector_settings_row()
        settings.collector_paused = request.form.get("enabled") is None
        db.session.commit()
    return _render_rtd_toggle()
