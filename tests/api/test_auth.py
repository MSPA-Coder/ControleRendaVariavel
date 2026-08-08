from __future__ import annotations

from collections.abc import Callable

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.models import User

pytestmark = [pytest.mark.critical, pytest.mark.security]


def test_health_check_does_not_require_authentication(client: FlaskClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_login_with_valid_credentials_starts_a_session(
    client: FlaskClient, test_user: User, test_password: str
) -> None:
    response = client.post(
        "/login",
        data={"username": "tester", "password": test_password},
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
    client: FlaskClient, test_user: User, test_password: str
) -> None:
    response = client.post(
        "/login",
        data={"username": "tester", "password": test_password},
    )

    set_cookie_headers = response.headers.getlist("Set-Cookie")
    assert any("controle_renda_variavel_session" in header for header in set_cookie_headers)
    assert not any("Secure" in header for header in set_cookie_headers)


def test_login_cookie_is_marked_secure_when_force_https_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
    app_factory: Callable[..., Flask],
    make_user: Callable[..., User],
    test_password: str,
) -> None:
    monkeypatch.setenv("FORCE_HTTPS", "true")
    https_app = app_factory()
    with https_app.app_context():
        make_user()

    response = https_app.test_client().post(
        "/login",
        data={"username": "tester", "password": test_password},
        headers={"X-Forwarded-Proto": "https"},
        environ_overrides={"wsgi.url_scheme": "https"},
    )

    set_cookie_headers = response.headers.getlist("Set-Cookie")
    assert any("Secure" in header for header in set_cookie_headers)
    remember_cookie = next(header for header in set_cookie_headers if "remember_token=" in header)
    assert "Secure" in remember_cookie
    assert "HttpOnly" in remember_cookie
    assert "SameSite=Lax" in remember_cookie


def test_login_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
    app_factory: Callable[..., Flask],
    make_user: Callable[..., User],
) -> None:
    monkeypatch.setenv("SECRET_KEY", "test-only-not-a-real-secret")
    limited_app = app_factory(RATELIMIT_ENABLED=True)
    with limited_app.app_context():
        make_user()

    client = limited_app.test_client()
    for _ in range(10):
        client.post("/login", data={"username": "tester", "password": "wrong"})

    response = client.post("/login", data={"username": "tester", "password": "wrong"})

    assert response.status_code == 429


def test_state_changing_post_without_csrf_token_is_rejected(
    app_factory: Callable[..., Flask],
    make_user: Callable[..., User],
) -> None:
    """CSRF fica desligado no resto da suíte para manter os testes diretos,
    então esta é a única prova de que a proteção está de fato ligada na
    configuração real: uma escrita autenticada sem token é recusada."""
    from flask_login import FlaskLoginClient  # type: ignore[import-untyped]

    csrf_app = app_factory(WTF_CSRF_ENABLED=True)
    with csrf_app.app_context():
        user = make_user()

    csrf_app.test_client_class = FlaskLoginClient
    response = csrf_app.test_client(user=user).post(
        "/tables/brokers", data={"name": "Genial", "acronym": "GEN"}
    )

    assert response.status_code == 400
