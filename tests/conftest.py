from __future__ import annotations

import os
from collections.abc import Callable, Iterator
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

_BASE_TEST_CONFIG: dict[str, object] = {
    "TESTING": True,
    "WTF_CSRF_ENABLED": False,
    "SQLALCHEMY_DATABASE_URI": _TEST_DATABASE_URL,
    "RATELIMIT_ENABLED": False,
}

TEST_PASSWORD = "correct-horse-battery-staple"


def build_test_app(**overrides: object) -> Flask:
    """A Flask app bound to the disposable test database.

    The schema is *not* rebuilt here: it is created once per session by
    ``_migrated_schema`` and cleaned between tests by ``app``. Tests that need
    a non-default configuration (forced HTTPS, rate limiting, ...) use this
    instead of repeating the connection and schema boilerplate.
    """

    return create_app({**_BASE_TEST_CONFIG, **overrides})


def _rebuild_schema() -> None:
    """Drop everything and rebuild the schema through the real migrations.

    Building the schema with Alembic rather than ``db.create_all()`` is what
    actually exercises the migrations, matching the project's testing policy.
    Requires an active application context.
    """

    with _db.engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA public CASCADE"))
        connection.execute(sa.text("CREATE SCHEMA public"))
    alembic_upgrade(directory=_MIGRATIONS_DIR)


_seed_metadata = sa.MetaData()
_seeded_rows: dict[str, list[dict[str, object]]] = {}


def _capture_seeded_rows() -> None:
    """Remember the rows the migrations themselves insert.

    Some revisions seed data (the singleton ``app_settings`` row, the default
    option expirations). A freshly migrated database therefore is not an empty
    one, and tests rely on that seed being present. It is captured once, right
    after the schema is built, so ``_reset_data`` can put it back.
    """

    _seed_metadata.clear()
    _seed_metadata.reflect(bind=_db.engine)
    _seeded_rows.clear()
    with _db.engine.connect() as connection:
        for table in _seed_metadata.sorted_tables:
            if table.name == "alembic_version":
                continue
            rows = [dict(row._mapping) for row in connection.execute(sa.select(table))]
            if rows:
                _seeded_rows[table.name] = rows


def _reset_data() -> None:
    """Return the database to its just-migrated state, schema untouched.

    Replaying the 14 migrations for every test dominated the suite's runtime.
    The schema is immutable for the whole session, so per-test isolation only
    needs the *data* reset: truncate everything, restore the migration seed,
    and realign the identity sequences so generated ids stay deterministic and
    never collide with a restored row.

    ``alembic_version`` is deliberately preserved: wiping it would make the
    session's schema look unmigrated to any later Alembic call.
    """

    with _db.engine.begin() as connection:
        table_names = connection.scalars(
            sa.text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).all()
        if not table_names:
            return
        targets = ", ".join(f'"{name}"' for name in table_names)
        connection.execute(sa.text(f"TRUNCATE TABLE {targets} RESTART IDENTITY CASCADE"))

        for table in _seed_metadata.sorted_tables:
            rows = _seeded_rows.get(table.name)
            if rows:
                connection.execute(table.insert(), rows)

        # TRUNCATE ... RESTART IDENTITY rewinds every sequence to 1, but the
        # restored rows already occupy their ids, so the next generated id
        # would collide. Push each sequence past the highest restored value.
        for table_name in _seeded_rows:
            connection.execute(
                sa.text(
                    "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                    "COALESCE((SELECT MAX(id) FROM " + f'"{table_name}"' + "), 1)) "
                    "WHERE pg_get_serial_sequence(:table_name, 'id') IS NOT NULL"
                ),
                {"table_name": table_name},
            )


@pytest.fixture(scope="session")
def _migrated_schema() -> Iterator[None]:
    """Create the test schema once for the whole session."""

    schema_app = build_test_app()
    with schema_app.app_context():
        try:
            _rebuild_schema()
        except sa.exc.OperationalError as exc:  # pragma: no cover - environment guard
            pytest.fail(
                "Não foi possível conectar ao PostgreSQL descartável de teste em "
                f"{_TEST_DATABASE_URL!r}. Suba-o com `docker compose up -d db` (e "
                "crie o banco 'investimentos_test') ou aponte TEST_DATABASE_URL "
                f"para uma instância descartável. Detalhe: {exc}"
            )
        _capture_seeded_rows()
        yield
        _db.session.remove()


@pytest.fixture()
def app(_migrated_schema: None) -> Iterator[Flask]:
    flask_app = build_test_app()
    with flask_app.app_context():
        _reset_data()
        yield flask_app
        _db.session.remove()


@pytest.fixture()
def app_factory(_migrated_schema: None) -> Iterator[Callable[..., Flask]]:
    """Build an app with custom configuration against the shared test schema.

    The data is cleared before the first app is built, so a test using this
    fixture starts from the same empty database as one using ``app``.
    """

    cleanup_app = build_test_app()
    with cleanup_app.app_context():
        _reset_data()
    yield build_test_app
    with cleanup_app.app_context():
        _db.session.remove()


@pytest.fixture()
def rebuild_schema(app: Flask) -> Iterator[None]:
    """For tests that deliberately migrate the schema up or down.

    They leave the database at a revision other than head, so the schema is
    rebuilt afterwards to keep the session-scoped schema valid for every
    later test.
    """

    yield
    with app.app_context():
        _rebuild_schema()


def create_user(username: str = "tester", password: str = TEST_PASSWORD) -> User:
    """Persist a user through the ORM. Requires an active application context."""

    user = User(username=username)
    user.set_password(password)
    _db.session.add(user)
    _db.session.commit()
    _db.session.refresh(user)
    return user


@pytest.fixture()
def test_password() -> str:
    return TEST_PASSWORD


@pytest.fixture()
def make_user() -> Callable[..., User]:
    """Seed a user into whichever app context is currently active."""

    return create_user


@pytest.fixture()
def db_session(app: Flask):  # type: ignore[no-untyped-def]
    with app.app_context():
        yield _db.session


@pytest.fixture()
def test_user(app: Flask, db_session) -> User:  # type: ignore[no-untyped-def]
    return create_user()


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture()
def auth_client(app: Flask, test_user: User) -> FlaskClient:
    app.test_client_class = FlaskLoginClient
    return app.test_client(user=test_user)
