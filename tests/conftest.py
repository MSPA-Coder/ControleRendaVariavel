from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from flask import Flask
from flask.testing import FlaskClient
from flask_login import FlaskLoginClient  # type: ignore[import-untyped]
from flask_migrate import upgrade as alembic_upgrade

from app import create_app
from app import db as _db
from app.models import User

# AGENTS.md / README.md are explicit: PostgreSQL is the only database used in
# tests that exercise persistence, and SQLite is never used to simulate it.
# TEST_DATABASE_URL lets CI point this at its own disposable Postgres service
# container; locally it defaults to a second database on the same disposable
# dev Postgres instance already used by `docker compose` (see README), so
# these tests never touch the operational "investimentos" database.
_TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://investimentos:investimentos@localhost:5435/investimentos_test",
)
_MIGRATIONS_DIR = str(Path(__file__).resolve().parent.parent / "migrations")


@pytest.fixture()
def app() -> Iterator[Flask]:
    flask_app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": _TEST_DATABASE_URL,
            "RATELIMIT_ENABLED": False,
        }
    )
    with flask_app.app_context():
        try:
            with _db.engine.begin() as connection:
                connection.execute(sa.text("DROP SCHEMA public CASCADE"))
                connection.execute(sa.text("CREATE SCHEMA public"))
        except sa.exc.OperationalError as exc:  # pragma: no cover - environment guard
            pytest.fail(
                "Não foi possível conectar ao PostgreSQL descartável de teste em "
                f"{_TEST_DATABASE_URL!r}. Suba-o com `docker compose up -d db` (e "
                "crie o banco 'investimentos_test') ou aponte TEST_DATABASE_URL "
                f"para uma instância descartável. Detalhe: {exc}"
            )
        # Building the schema through the real Alembic revisions (rather than
        # db.create_all()) is what actually exercises migrations, matching
        # the project's testing policy.
        alembic_upgrade(directory=_MIGRATIONS_DIR)
        yield flask_app
        _db.session.remove()


@pytest.fixture()
def db_session(app: Flask):  # type: ignore[no-untyped-def]
    with app.app_context():
        yield _db.session


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture()
def test_user(app: Flask, db_session) -> User:  # type: ignore[no-untyped-def]
    user = User(username="tester")
    user.set_password("correct-horse-battery-staple")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def auth_client(app: Flask, test_user: User) -> FlaskClient:
    app.test_client_class = FlaskLoginClient
    return app.test_client(user=test_user)
