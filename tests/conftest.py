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
_BASE_TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://investimentos:investimentos@localhost:5435/investimentos_test",
)

# Com pytest-xdist cada worker recebe um banco próprio. Compartilhar um só
# banco quebraria o isolamento: o TRUNCATE de um worker apagaria os dados que
# outro acabou de semear. O processo único (sem xdist) usa o banco base.
_XDIST_WORKER = os.getenv("PYTEST_XDIST_WORKER", "")


def _worker_database_url() -> str:
    if not _XDIST_WORKER:
        return _BASE_TEST_DATABASE_URL
    base, _, name = _BASE_TEST_DATABASE_URL.rpartition("/")
    return f"{base}/{name}_{_XDIST_WORKER}"


_TEST_DATABASE_URL = _worker_database_url()


def _ensure_worker_database() -> None:
    """Cria o banco deste worker, se ainda não existir.

    ``CREATE DATABASE`` não roda dentro de transação, daí o AUTOCOMMIT, e
    precisa de outra conexão para ser emitido — usamos o banco base.
    """

    if not _XDIST_WORKER:
        return
    database = _TEST_DATABASE_URL.rpartition("/")[2]
    admin = sa.create_engine(_BASE_TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.scalar(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database},
            )
            if not exists:
                connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
    finally:
        admin.dispose()
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

    The SQL is assembled once (``_reset_statements``) because the table list
    cannot change during a session: rebuilding it per test meant an extra
    round trip to ``pg_tables`` before every single test.

    ``alembic_version`` is deliberately preserved: wiping it would make the
    session's schema look unmigrated to any later Alembic call.
    """

    truncate, setval = _reset_statements()
    if truncate is None:
        return
    with _db.engine.begin() as connection:
        connection.execute(truncate)
        for table in _seed_metadata.sorted_tables:
            rows = _seeded_rows.get(table.name)
            if rows:
                connection.execute(table.insert(), rows)
        if setval is not None:
            connection.execute(setval)


_reset_sql: tuple[sa.TextClause | None, sa.TextClause | None] | None = None


def _reset_statements() -> tuple[sa.TextClause | None, sa.TextClause | None]:
    global _reset_sql
    if _reset_sql is not None:
        return _reset_sql
    with _db.engine.connect() as connection:
        table_names = connection.scalars(
            sa.text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).all()
    if not table_names:
        _reset_sql = (None, None)
        return _reset_sql
    targets = ", ".join(f'"{name}"' for name in table_names)
    truncate = sa.text(f"TRUNCATE TABLE {targets} RESTART IDENTITY CASCADE")
    # Uma única ida ao banco realinha todas as sequências das tabelas semeadas.
    seeded = [name for name in table_names if name in _seeded_rows]
    setval = None
    if seeded:
        parts = [
            f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
            f'COALESCE((SELECT MAX(id) FROM "{name}"), 1)) '
            f"WHERE pg_get_serial_sequence('{name}', 'id') IS NOT NULL"
            for name in seeded
        ]
        setval = sa.text(" UNION ALL ".join(f"({part})" for part in parts))
    _reset_sql = (truncate, setval)
    return _reset_sql


@pytest.fixture(scope="session")
def _migrated_schema() -> Iterator[Flask]:
    """Build the schema once and keep one application for the whole session.

    Criar um ``Flask`` por teste custava caro: cada aplicação traz um engine
    SQLAlchemy novo, e portanto um pool de conexões novo. Como a configuração
    de teste é sempre a mesma, uma única aplicação atende todos os testes; os
    que precisam de configuração diferente usam ``app_factory``.
    """

    _ensure_worker_database()
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
        yield schema_app
        _db.session.remove()


@pytest.fixture()
def app(_migrated_schema: Flask) -> Iterator[Flask]:
    """A aplicação compartilhada, com dados e estado mutável reiniciados.

    Como a instância é reaproveitada, o que um teste altera nela precisa ser
    desfeito: ``config`` e ``test_client_class`` são restaurados no teardown,
    senão um teste que autentica mudaria o cliente de todos os seguintes.
    """

    flask_app = _migrated_schema
    original_config = dict(flask_app.config)
    original_client_class = flask_app.test_client_class
    with flask_app.app_context():
        _reset_data()
        try:
            yield flask_app
        finally:
            _db.session.remove()
            flask_app.test_client_class = original_client_class
            flask_app.config.clear()
            flask_app.config.update(original_config)


@pytest.fixture()
def app_factory(_migrated_schema: Flask) -> Iterator[Callable[..., Flask]]:
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
