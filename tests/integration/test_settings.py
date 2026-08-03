from __future__ import annotations

from decimal import Decimal

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models import AppSetting, Market, Ticker
from app.rtd_service import OperationalProfile


class FakeRtdService:
    def __init__(
        self,
        profile: OperationalProfile = OperationalProfile.TEST,
        *,
        unavailable: bool = False,
        set_error: RuntimeError | None = None,
    ) -> None:
        self.profile = profile
        self.unavailable = unavailable
        self.set_error = set_error
        self.profile_updates: list[OperationalProfile] = []

    @property
    def operational_profile(self) -> OperationalProfile:
        if self.unavailable:
            raise RuntimeError("controlador indisponivel")
        return self.profile

    def set_operational_profile(self, profile: OperationalProfile) -> None:
        if self.set_error is not None:
            raise self.set_error
        self.profile_updates.append(profile)
        self.profile = profile


def _settings_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "operational_profile": "test",
        "collector_mode": "excel",
        "poll_interval_seconds": "2",
        "risk_free_rate_annual": "0.12",
    }
    payload.update(overrides)
    return payload


def _seed_ticker(symbol: str = "IBOV") -> int:
    ticker = Ticker(
        symbol=symbol,
        trading_name=symbol,
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    db.session.add(ticker)
    db.session.commit()
    return ticker.id


def test_settings_page_shows_default_risk_free_rate(auth_client: FlaskClient) -> None:
    response = auth_client.get("/settings")

    assert response.status_code == 200
    assert b"risk_free_rate_annual" in response.data


def test_settings_shows_host_operational_profile(app: Flask, auth_client: FlaskClient) -> None:
    service = FakeRtdService(OperationalProfile.PRODUCTION)
    app.extensions["rtd_service"] = service

    response = auth_client.get("/settings")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Desenvolvimento/Testes" in page
    assert "Produção automática" in page
    assert 'value="production"\n             checked' in page
    assert 'value="test"\n             checked' not in page


def test_settings_shows_unavailable_host_without_guessing_profile(
    app: Flask, auth_client: FlaskClient
) -> None:
    app.extensions["rtd_service"] = FakeRtdService(unavailable=True)

    response = auth_client.get("/settings")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Perfil indisponível" in page
    assert 'name="operational_profile" value="test"\n             \n             disabled' in page
    assert (
        'name="operational_profile" value="production"\n             \n             disabled'
        in page
    )


def test_settings_updates_host_profile_before_persisting_other_settings(
    app: Flask, auth_client: FlaskClient
) -> None:
    service = FakeRtdService()
    app.extensions["rtd_service"] = service

    response = auth_client.post(
        "/settings", data=_settings_payload(operational_profile="production")
    )

    assert response.status_code == 302
    assert service.profile_updates == [OperationalProfile.PRODUCTION]
    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        assert settings is not None
        assert settings.risk_free_rate_annual == Decimal("0.12")


def test_settings_does_not_persist_other_settings_when_host_profile_fails(
    app: Flask, auth_client: FlaskClient
) -> None:
    auth_client.get("/settings")
    app.extensions["rtd_service"] = FakeRtdService(
        set_error=RuntimeError("controlador indisponivel")
    )

    response = auth_client.post(
        "/settings",
        data=_settings_payload(operational_profile="production", risk_free_rate_annual="0.22"),
    )

    assert response.status_code == 503
    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        assert settings is not None
        assert settings.risk_free_rate_annual == Decimal("0.1075")


def test_settings_restores_host_profile_when_database_commit_fails(
    app: Flask, auth_client: FlaskClient, monkeypatch
) -> None:
    auth_client.get("/settings")
    service = FakeRtdService()
    app.extensions["rtd_service"] = service

    def fail_commit() -> None:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(db.session, "commit", fail_commit)
    response = auth_client.post(
        "/settings", data=_settings_payload(operational_profile="production")
    )

    assert response.status_code == 503
    assert service.profile_updates == [OperationalProfile.PRODUCTION, OperationalProfile.TEST]


def test_settings_updates_risk_free_rate(app: Flask, auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/settings",
        data={
            "operational_profile": "test",
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "0.12",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        assert settings is not None
        assert settings.risk_free_rate_annual == Decimal("0.12")


def test_settings_rejects_risk_free_rate_out_of_range(
    app: Flask, auth_client: FlaskClient
) -> None:
    response = auth_client.post(
        "/settings",
        data={
            "operational_profile": "test",
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "1.5",
        },
    )

    assert response.status_code == 422
    assert b"taxa livre de risco" in response.data.lower()
    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        # Nada foi persistido: continua no valor padrão.
        assert settings is not None
        assert settings.risk_free_rate_annual == Decimal("0.1075")


def test_settings_requires_authentication(client: FlaskClient) -> None:
    response = client.get("/settings")

    assert response.status_code == 302


def test_settings_sets_benchmark_ticker_id(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        ticker_id = _seed_ticker()

    response = auth_client.post(
        "/settings",
        data={
            "operational_profile": "test",
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "0.12",
            "benchmark_ticker_id": str(ticker_id),
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        assert settings is not None
        assert settings.benchmark_ticker_id == ticker_id


def test_settings_clears_benchmark_ticker_id(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        ticker_id = _seed_ticker()
    auth_client.post(
        "/settings",
        data={
            "operational_profile": "test",
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "0.12",
            "benchmark_ticker_id": str(ticker_id),
        },
    )

    auth_client.post(
        "/settings",
        data={
            "operational_profile": "test",
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "0.12",
            "benchmark_ticker_id": "",
        },
    )

    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        assert settings is not None
        assert settings.benchmark_ticker_id is None


def test_settings_rejects_unknown_benchmark_ticker_id(
    app: Flask, auth_client: FlaskClient
) -> None:
    response = auth_client.post(
        "/settings",
        data={
            "operational_profile": "test",
            "collector_mode": "excel",
            "poll_interval_seconds": "2",
            "risk_free_rate_annual": "0.12",
            "benchmark_ticker_id": "999999",
        },
    )

    assert response.status_code == 422
    assert "referência para o beta".encode() in response.data.lower()
    with app.app_context():
        settings = db.session.get(AppSetting, 1)
        assert settings is not None
        assert settings.benchmark_ticker_id is None
