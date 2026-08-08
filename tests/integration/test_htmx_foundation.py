from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.routes.helpers import is_htmx_request

pytestmark = [pytest.mark.smoke]


def test_htmx_is_served_locally_and_version_pinned(auth_client: FlaskClient) -> None:
    """A base de engenharia exige asset local com versão fixa: nada de CDN
    nem de `latest`, que a CSP `default-src 'self'` também bloquearia."""
    html = auth_client.get("/").get_data(as_text=True)

    assert "vendor/htmx.min.js" in html
    assert "unpkg.com" not in html and "jsdelivr" not in html

    asset = auth_client.get("/static/vendor/htmx.min.js")
    assert asset.status_code == 200
    assert b"htmx" in asset.data


@pytest.mark.security
@pytest.mark.critical
def test_htmx_requests_carry_the_csrf_token(auth_client: FlaskClient) -> None:
    """Toda escrita vinda do navegador precisa de CSRF. O token é entregue
    por atributo `hx-headers`, e não por handler inline, porque a CSP do
    projeto não permite script inline."""
    html = auth_client.get("/").get_data(as_text=True)

    assert "hx-headers=" in html
    assert "X-CSRFToken" in html


@pytest.mark.security
@pytest.mark.critical
def test_csp_still_forbids_inline_and_external_script(auth_client: FlaskClient) -> None:
    policy = auth_client.get("/").headers["Content-Security-Policy"]

    assert "default-src 'self'" in policy
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy


def test_is_htmx_request_reads_the_header(app: Flask) -> None:
    with app.test_request_context("/", headers={"HX-Request": "true"}):
        assert is_htmx_request() is True

    with app.test_request_context("/"):
        assert is_htmx_request() is False


@pytest.mark.security
@pytest.mark.critical
def test_htmx_header_does_not_grant_access(client: FlaskClient) -> None:
    """`HX-Request` é negociação de apresentação, nunca autorização: sem
    sessão, a resposta continua sendo redirecionamento para o login."""
    response = client.get("/", headers={"HX-Request": "true"})

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.critical
def test_fonts_are_self_hosted(auth_client: FlaskClient) -> None:
    """A CSP é `default-src 'self'` e não abre exceção para fontes: enquanto
    as famílias vinham do Google Fonts, o navegador as bloqueava e a
    interface caía silenciosamente nas fontes do sistema. Servir da própria
    origem é o que faz o design pretendido realmente aparecer."""
    html = auth_client.get("/").get_data(as_text=True)

    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert "fonts.css" in html

    stylesheet = auth_client.get("/static/fonts.css")
    assert stylesheet.status_code == 200
    body = stylesheet.get_data(as_text=True)
    assert "@font-face" in body
    assert "https://" not in body, "nenhuma URL externa deve sobrar no CSS de fontes"

    # E o arquivo referenciado precisa existir de fato.
    assert auth_client.get("/static/vendor/fonts/inter-100-900-latin.woff2").status_code == 200
