import pytest

from app import create_app


@pytest.mark.parametrize(
    "template_name",
    [
        "base.html",
        "index.html",
        "options.html",
        "tables.html",
        "login.html",
        "transactions.html",
        "transaction_form.html",
        "dividends.html",
        "dividend_form.html",
        "close_position_form.html",
        "quotes.html",
        "risk.html",
        "performance.html",
    ],
)
def test_main_templates_compile(template_name: str) -> None:
    app = create_app({"TESTING": True})

    app.jinja_env.get_template(template_name)


def test_session_cookie_is_not_secure_by_default() -> None:
    # The app is deployed over plain HTTP on a local network by default (see
    # README); a Secure cookie flag here would silently break login, since
    # browsers refuse to send Secure cookies back over HTTP.
    app = create_app({"TESTING": True})

    assert app.config["SESSION_COOKIE_SECURE"] is False
