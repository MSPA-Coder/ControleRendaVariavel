from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from flask import Flask, jsonify, redirect, request, url_for
from flask.typing import ResponseReturnValue
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user  # type: ignore[import-untyped]
from flask_migrate import Migrate  # type: ignore[import-untyped]
from flask_sqlalchemy import SQLAlchemy
from flask_talisman import Talisman  # type: ignore[import-untyped]
from flask_wtf.csrf import CSRFProtect  # type: ignore[import-untyped]
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix

if TYPE_CHECKING:
    from app.models import User

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    pass


Base.metadata.naming_convention = convention
db = SQLAlchemy(model_class=Base)
migrate = Migrate()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Faça login para continuar."
login_manager.login_message_category = "error"
limiter = Limiter(key_func=get_remote_address)

# Endpoints reachable without an authenticated session. Kept intentionally
# small: everything else in the app shows personal financial data.
PUBLIC_ENDPOINTS = {"auth.login", "portfolio.health", "static"}


@login_manager.user_loader  # type: ignore[misc]
def _load_user(user_id: str) -> User | None:
    from app.models import User

    return db.session.get(User, int(user_id))


def create_app(config: dict[str, object] | None = None) -> Flask:
    app = Flask(__name__)
    force_https = os.getenv("FORCE_HTTPS", "false").lower() == "true"
    configured_secret_key = os.getenv("SECRET_KEY")
    app.config.from_mapping(
        SECRET_KEY=configured_secret_key,
        SESSION_COOKIE_NAME=(
            os.getenv(
                "RENDA_VARIAVEL_SESSION_COOKIE_NAME",
                "controle_renda_variavel_session",
            ).strip()
            or "controle_renda_variavel_session"
        ),
        SQLALCHEMY_DATABASE_URI=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://investimentos:investimentos@localhost:5435/investimentos",
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        RTD_PROG_ID=os.getenv("RTD_PROG_ID", "rtdtrading.rtdserver"),
        RTD_REFRESH_SECONDS=float(os.getenv("RTD_REFRESH_SECONDS", "2")),
        RTD_TIMEOUT_SECONDS=float(os.getenv("RTD_TIMEOUT_SECONDS", "10")),
        RTD_STALE_AFTER_SECONDS=int(os.getenv("RTD_STALE_AFTER_SECONDS", "30")),
        RTD_EXCEL_VISIBLE=os.getenv("RTD_EXCEL_VISIBLE", "false").lower() == "true",
        RTD_CONTROL_URL=os.getenv("RTD_CONTROL_URL", ""),
        RTD_CONTROL_TOKEN=os.getenv("RTD_CONTROL_TOKEN", ""),
        FORCE_HTTPS=force_https,
        TRUST_PROXY_HEADERS=os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true",
        RATELIMIT_STORAGE_URI=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
        RATELIMIT_ENABLED=os.getenv("RATELIMIT_ENABLED", "true").lower() == "true",
    )
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"]:
        if app.config["TESTING"]:
            app.config["SECRET_KEY"] = "test-only-not-a-real-secret"
        else:
            raise RuntimeError("Defina SECRET_KEY antes de iniciar a aplicação.")
    app.config["REMEMBER_COOKIE_SECURE"] = app.config["FORCE_HTTPS"]
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"

    if app.config["TRUST_PROXY_HEADERS"]:
        # Only trust X-Forwarded-* when actually deployed behind a reverse
        # proxy that sets them (Caddy/nginx terminating TLS).
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app, x_for=1, x_proto=1, x_host=1
        )

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)
    Talisman(
        app,
        force_https=app.config["FORCE_HTTPS"],
        strict_transport_security=app.config["FORCE_HTTPS"],
        # Browsers silently drop cookies marked Secure when served over plain
        # HTTP, which would break login. Only require it once FORCE_HTTPS is
        # actually turned on (e.g. behind a Caddy/nginx TLS proxy).
        session_cookie_secure=app.config["FORCE_HTTPS"],
        # No inline <script>/<style> is used anywhere in app/templates, so
        # the default same-origin CSP applies cleanly without 'unsafe-inline'.
        content_security_policy={"default-src": "'self'", "object-src": "'none'"},
    )

    from app.rtd_service import RemoteRtdService, RtdServiceManager

    control_url = str(app.config["RTD_CONTROL_URL"])
    control_token = str(app.config["RTD_CONTROL_TOKEN"])
    if control_url and control_token:
        app.extensions["rtd_service"] = RemoteRtdService(control_url, control_token)
    else:
        app.extensions["rtd_service"] = RtdServiceManager(Path(app.root_path).parent)

    from app.cli import register_commands
    from app.presentation import register_filters
    from app.routes import register_blueprints

    register_blueprints(app)
    register_commands(app)
    register_filters(app)

    @app.context_processor
    def _collector_heartbeat_context() -> dict[str, object]:
        from app.collector_heartbeat import collector_heartbeat

        return {
            "collector_heartbeat": collector_heartbeat(
                stale_after_seconds=app.config["RTD_STALE_AFTER_SECONDS"]
            )
        }

    @app.before_request
    def _require_login() -> ResponseReturnValue | None:
        if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if current_user.is_authenticated:
            return None
        if request.path.startswith("/api/"):
            return jsonify(error="Autenticação necessária."), 401
        return redirect(url_for("auth.login", next=request.full_path))

    return app
