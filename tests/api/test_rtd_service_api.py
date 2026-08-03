from __future__ import annotations

from unittest.mock import Mock

from flask import Flask
from flask.testing import FlaskClient


def test_rtd_service_api_requires_authentication(client: FlaskClient) -> None:
    response = client.get("/api/rtd-service")

    assert response.status_code == 401


def test_rtd_service_api_reports_unavailable_controller_without_500(
    app: Flask, auth_client: FlaskClient
) -> None:
    service = Mock()
    type(service).is_running = property(
        lambda _service: (_ for _ in ()).throw(RuntimeError("indisponível"))
    )
    app.extensions["rtd_service"] = service

    response = auth_client.get("/api/rtd-service")

    assert response.status_code == 503
    assert response.get_json() == {
        "available": False,
        "error": "indisponível",
        "running": False,
        "status": "unavailable",
    }


def test_rtd_service_api_toggle_accepts_valid_payload(
    app: Flask, auth_client: FlaskClient
) -> None:
    service = Mock()
    service.is_running = True
    service.available = True
    service.status = "running"
    app.extensions["rtd_service"] = service

    response = auth_client.post("/api/rtd-service", json={"enabled": True})

    assert response.status_code == 200
    assert response.get_json()["status"] == "running"
    service.start.assert_called_once_with()


def test_rtd_service_api_toggle_rejects_invalid_payload(
    app: Flask, auth_client: FlaskClient
) -> None:
    service = Mock()
    app.extensions["rtd_service"] = service

    response = auth_client.post("/api/rtd-service", json={"enabled": "yes-please"})

    assert response.status_code == 400
    service.start.assert_not_called()
    service.stop.assert_not_called()
