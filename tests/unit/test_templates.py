import pytest

from app import create_app

pytestmark = [pytest.mark.interface_smoke]


@pytest.mark.parametrize(
    "template_name",
    [
        "base.html",
        "index.html",
        "options.html",
        "table_brokers.html",
        "table_tickers.html",
        "table_expirations.html",
        "table_contracts.html",
        "login.html",
        "transactions.html",
        "transaction_form.html",
        "dividends.html",
        "dividend_form.html",
        "close_position_form.html",
        "quotes.html",
        "risk.html",
        "performance.html",
        "settings.html",
        "exposure_asset.html",
        "exposure_broker.html",
        "exposure_market.html",
        "partials/exposure.html",
    ],
)
def test_main_templates_compile(template_name: str) -> None:
    app = create_app({"TESTING": True})

    app.jinja_env.get_template(template_name)


@pytest.mark.security
@pytest.mark.critical
def test_session_cookie_is_not_secure_by_default() -> None:
    # The app is deployed over plain HTTP on a local network by default (see
    # README); a Secure cookie flag here would silently break login, since
    # browsers refuse to send Secure cookies back over HTTP.
    app = create_app({"TESTING": True})

    assert app.config["SESSION_COOKIE_SECURE"] is False
