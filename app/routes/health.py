from __future__ import annotations

from flask import jsonify
from flask.typing import ResponseReturnValue
from sqlalchemy import select

from app import db
from app.routes import bp


@bp.get("/health")
def health() -> ResponseReturnValue:
    db.session.execute(select(1))
    return jsonify(status="ok")
