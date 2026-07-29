from __future__ import annotations

import os
from pathlib import Path

from flask import Flask
from flask_migrate import Migrate  # type: ignore[import-untyped]
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect  # type: ignore[import-untyped]
from sqlalchemy.orm import DeclarativeBase

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


def create_app(config: dict[str, object] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "development-only-change-me"),
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
    )
    if config:
        app.config.update(config)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    from app.rtd_service import RemoteRtdService, RtdServiceManager

    control_url = str(app.config["RTD_CONTROL_URL"])
    control_token = str(app.config["RTD_CONTROL_TOKEN"])
    if control_url and control_token:
        app.extensions["rtd_service"] = RemoteRtdService(control_url, control_token)
    else:
        app.extensions["rtd_service"] = RtdServiceManager(Path(app.root_path).parent)

    from app.cli import register_commands
    from app.options_web import bp as options_bp
    from app.presentation import register_filters
    from app.web import bp

    app.register_blueprint(bp)
    app.register_blueprint(options_bp)
    register_commands(app)
    register_filters(app)
    return app
