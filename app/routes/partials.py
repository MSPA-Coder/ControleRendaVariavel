"""Fragmentos HTML consumidos pelo HTMX.

Estas rotas devolvem pedaços de página, não JSON: o servidor continua sendo
quem renderiza e quem decide o que o usuário vê. Elas usam os mesmos macros
dos templates completos, então não existe uma segunda cópia da apresentação
para manter em sincronia.

Autorização é a de sempre — ``_require_login`` protege todas as rotas do
app. O cabeçalho ``HX-Request`` nunca é consultado aqui como permissão.
"""

from __future__ import annotations

from flask import abort, current_app, render_template, request
from flask.typing import ResponseReturnValue

from app import db
from app.accounts.authorization import requer_admin
from app.collector.database import collector_settings_row
from app.routes import bp
from app.routes.helpers import collector_is_enabled


def _render_heartbeat() -> str:
    # O pulso vem do processador de contexto (`app._collector_heartbeat_context`).
    return render_template("partials/collector_heartbeat.html")


def _render_rtd_toggle() -> str:
    return render_template(
        "partials/rtd_toggle.html",
        collector_enabled=collector_is_enabled(),
        remote_collector_enabled=current_app.config["REMOTE_COLLECTOR_ENABLED"],
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

    Somente o VPS oferece pausa/retomada. No local, iniciar e parar são
    ações explícitas do Windows; não há processo esperando um checkbox.
    """
    if request.method == "POST":
        if not current_app.config["REMOTE_COLLECTOR_ENABLED"]:
            abort(409, description="Inicie ou pare a coleta local no Windows.")
        settings = collector_settings_row()
        settings.collector_paused = request.form.get("enabled") is None
        db.session.commit()
    return _render_rtd_toggle()
