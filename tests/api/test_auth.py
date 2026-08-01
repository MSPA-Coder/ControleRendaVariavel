from __future__ import annotations

import os
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from app.models import User

_TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://investimentos:investimentos@localhost:5435/investimentos_test",
)
_MIGRATIONS_DIR = str(Path(__file__).resolve().parents[2] / "migrations")


def test_health_check_does_not_require_authentication(client: FlaskClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_login_with_valid_credentials_starts_a_session(
    client: FlaskClient, test_user: User
) -> None:
    response = client.post(
        "/login",
        data={"username": "tester", "password": "correct-horse-battery-staple"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    dashboard = client.get("/")
    assert dashboard.status_code == 200


def test_login_with_invalid_credentials_is_rejected(
    client: FlaskClient, test_user: User
) -> None:
    response = client.post(
        "/login", data={"username": "tester", "password": "wrong-password"}
    )

    assert response.status_code == 401
    # And the protected dashboard should still redirect to login afterwards.
    assert client.get("/").status_code == 302


def test_logout_ends_the_session(auth_client: FlaskClient) -> None:
    assert auth_client.get("/").status_code == 200

    auth_client.post("/logout")

    assert auth_client.get("/").status_code == 302


def test_login_cookie_is_not_marked_secure_over_plain_http(
    client: FlaskClient, test_user: User
) -> None:
    response = client.post(
        "/login",
        data={"username": "tester", "password": "correct-horse-battery-staple"},
    )

    set_cookie_headers = response.headers.getlist("Set-Cookie")
    assert any("controle_renda_variavel_session" in header for header in set_cookie_headers)
    assert not any("Secure" in header for header in set_cookie_headers)


def test_login_cookie_is_marked_secure_when_force_https_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlalchemy as sa
    from flask_migrate import upgrade as alembic_upgrade

    from app import create_app
    from app import db as _db

    monkeypatch.setenv("FORCE_HTTPS", "true")
    https_app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": _TEST_DATABASE_URL,
        }
    )
    with https_app.app_context():
        with _db.engine.begin() as connection:
            connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            connection.execute(sa.text("CREATE SCHEMA public"))
        alembic_upgrade(directory=_MIGRATIONS_DIR)
        user = User(username="tester")
        user.set_password("correct-horse-battery-staple")
        _db.session.add(user)
        _db.session.commit()

    response = https_app.test_client().post(
        "/login",
        data={"username": "tester", "password": "correct-horse-battery-staple"},
        headers={"X-Forwarded-Proto": "https"},
        environ_overrides={"wsgi.url_scheme": "https"},
    )

    set_cookie_headers = response.headers.getlist("Set-Cookie")
    assert any("Secure" in header for header in set_cookie_headers)
    remember_cookie = next(header for header in set_cookie_headers if "remember_token=" in header)
    assert "Secure" in remember_cookie
    assert "HttpOnly" in remember_cookie
    assert "SameSite=Lax" in remember_cookie


def test_login_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlalchemy as sa
    from flask_migrate import upgrade as alembic_upgrade

    from app import create_app
    from app import db as _db

    monkeypatch.setenv("SECRET_KEY", "test-only-not-a-real-secret")
    limited_app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": _TEST_DATABASE_URL,
            "RATELIMIT_ENABLED": True,
        }
    )
    with limited_app.app_context():
        with _db.engine.begin() as connection:
            connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            connection.execute(sa.text("CREATE SCHEMA public"))
        alembic_upgrade(directory=_MIGRATIONS_DIR)
        user = User(username="tester")
        user.set_password("correct-horse-battery-staple")
        _db.session.add(user)
        _db.session.commit()

    client = limited_app.test_client()
    for _ in range(10):
        client.post("/login", data={"username": "tester", "password": "wrong"})

    response = client.post("/login", data={"username": "tester", "password": "wrong"})

    assert response.status_code == 429
