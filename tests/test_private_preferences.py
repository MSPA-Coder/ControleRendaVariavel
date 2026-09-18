"""Preferências individuais não alteram outra conta nem a infraestrutura."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app import db
from app.collector.settings import default_collector_settings
from app.models import AppSetting, Market, Ticker, User, UserPreference, UserTickerEntitlement

pytestmark = pytest.mark.banco


@pytest.fixture
def preference_accounts(app_com_banco):
    with app_com_banco.app_context():
        suffix = uuid4().hex
        users = [User(username=f"prefs-{suffix}-{role}", role=role) for role in ("operador", "admin")]
        for user in users:
            user.set_password("Synthetic-preferences-only-2026!")
        db.session.add_all(users)
        settings = db.session.get(AppSetting, 1)
        if settings is None:
            settings = default_collector_settings()
            db.session.add(settings)
        db.session.commit()
        ids = [user.id for user in users]
        session_id = users[0].get_id()
        global_values = (settings.theme, settings.risk_free_rate_annual, settings.poll_interval_seconds)
        db.session.add(UserPreference(user_id=ids[1], theme="light"))
        db.session.commit()
    client = app_com_banco.test_client()
    # Cada cenário tem endereço sintético próprio; a proteção de login continua ativa.
    client.environ_base['REMOTE_ADDR'] = '2001:db8::' + ':'.join(suffix[i:i + 4] for i in range(0, 16, 4))
    login = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', login.get_data(as_text=True)).group(1)
    with client.session_transaction() as session:
        session["_user_id"] = session_id
        session["_fresh"] = True
    try:
        yield app_com_banco, client, csrf, ids, global_values
    finally:
        with app_com_banco.app_context():
            db.session.execute(delete(UserPreference).where(UserPreference.user_id.in_(ids)))
            db.session.execute(delete(User).where(User.id.in_(ids)))
            db.session.commit()


def test_operator_can_save_only_own_preferences(preference_accounts):
    app, client, csrf, ids, global_values = preference_accounts
    assert client.get("/preferences").status_code == 200
    assert client.get("/settings").status_code == 403
    response = client.post("/preferences", data={
        "csrf_token": csrf, "user_id": ids[1], "owner_id": ids[1],
        "theme": "dark", "risk_free_rate_annual": "0.2", "stale_alert_seconds": "120",
        "benchmark_ticker_id": "", "poll_interval_seconds": "999", "collector_paused": "true",
    })
    assert response.status_code == 302
    with app.app_context():
        own = db.session.get(UserPreference, ids[0])
        other = db.session.get(UserPreference, ids[1])
        assert (own.theme, own.risk_free_rate_annual, own.stale_alert_seconds) == ("dark", Decimal("0.2"), None)
        assert other.theme == "light"
        settings = db.session.get(AppSetting, 1)
        assert (settings.theme, settings.risk_free_rate_annual, settings.poll_interval_seconds) == global_values
    page = client.get("/preferences").get_data(as_text=True)
    assert 'data-theme="dark"' in page
    assert 'class="theme-grid"' in page
    assert "Alertar cotação desatualizada" not in page


@pytest.mark.parametrize("rate", ["NaN", "999999999999999999999", "²"])
def test_invalid_preferences_do_not_persist(preference_accounts, rate):
    app, client, csrf, ids, _ = preference_accounts
    response = client.post("/preferences", data={
        "csrf_token": csrf, "theme": "dark", "risk_free_rate_annual": rate,
        "benchmark_ticker_id": "",
    })
    assert response.status_code == 422
    with app.app_context():
        own = db.session.get(UserPreference, ids[0])
        assert own is None or own.theme != "dark"


def test_preferences_require_login_and_csrf(preference_accounts):
    app, client, _, ids, _ = preference_accounts
    anonymous = app.test_client()
    response = anonymous.get("/preferences")
    assert response.status_code == 302 and "/login" in response.location
    assert client.post("/preferences", data={"theme": "dark"}).status_code == 400
    with app.app_context():
        assert db.session.get(UserPreference, ids[0]) is None


def test_benchmark_requires_own_historical_entitlement(preference_accounts):
    app, client, csrf, ids, _ = preference_accounts
    with app.app_context():
        ticker = Ticker(symbol=f"P{uuid4().hex[:16]}", trading_name="Referência de teste",
                        market=Market.B3, rtd_market_code="B", currency="BRL")
        db.session.add(ticker)
        db.session.flush()
        ticker_id = ticker.id
        ticker_symbol = ticker.symbol
        db.session.add(UserTickerEntitlement(user_id=ids[1], ticker_id=ticker_id, first_held_on=date(2026, 1, 1)))
        db.session.commit()
    form = {"csrf_token": csrf, "theme": "light", "risk_free_rate_annual": "0.1",
            "benchmark_ticker_id": ticker_id}
    try:
        assert client.post("/preferences", data=form).status_code == 422
        with app.app_context():
            preference = db.session.get(UserPreference, ids[0])
            assert preference is None or preference.benchmark_ticker_id is None
            if preference is None:
                preference = UserPreference(user_id=ids[0])
                db.session.add(preference)
            preference.benchmark_ticker_id = ticker_id
            db.session.commit()
        risk = client.get("/risk")
        assert risk.status_code == 200
        assert ticker_symbol not in risk.get_data(as_text=True)
        with app.app_context():
            db.session.add(UserTickerEntitlement(user_id=ids[0], ticker_id=ticker_id, first_held_on=date(2026, 1, 1)))
            db.session.commit()
        assert client.post("/preferences", data=form).status_code == 302
        with app.app_context():
            assert db.session.get(UserPreference, ids[0]).benchmark_ticker_id == ticker_id
    finally:
        with app.app_context():
            db.session.execute(delete(UserTickerEntitlement).where(UserTickerEntitlement.ticker_id == ticker_id))
            db.session.execute(delete(Ticker).where(Ticker.id == ticker_id))
            db.session.commit()
