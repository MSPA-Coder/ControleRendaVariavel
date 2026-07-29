from __future__ import annotations

from flask import Blueprint, Flask

# Shared blueprint for the main "portfolio" namespace. It is intentionally
# defined here (rather than in one of the route modules below) so that
# positions.py, tables.py, settings.py, api.py and health.py can each attach
# a slice of routes to the *same* blueprint instance. This keeps endpoint
# names stable as ``portfolio.<view_name>`` even though the implementation is
# split across several files, so templates using ``url_for('portfolio.x')``
# do not need to change.
bp = Blueprint("portfolio", __name__)


def register_blueprints(app: Flask) -> None:
    """Import route modules (registering their views) and mount blueprints."""

    # Importing these modules has the side effect of attaching their view
    # functions to ``bp`` (or, for options/auth, to their own blueprint).
    from app.routes import api, health, positions, settings, tables  # noqa: F401
    from app.routes.auth import bp as auth_bp
    from app.routes.options import bp as options_bp

    app.register_blueprint(bp)
    app.register_blueprint(options_bp)
    app.register_blueprint(auth_bp)
