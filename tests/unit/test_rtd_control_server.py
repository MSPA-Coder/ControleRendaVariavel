from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app.rtd_control_server import _handler
from app.rtd_service import OperationalProfile


@pytest.fixture
def control_server() -> tuple[str, Mock]:
    service = Mock()
    service.operational_profile = OperationalProfile.TEST
    service.is_running = False
    service.status = "test_idle"
    service.automation_status = "disabled"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(service, "secret-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", service
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.security
@pytest.mark.critical
def test_profile_endpoint_is_authenticated(control_server: tuple[str, Mock]) -> None:
    base_url, _service = control_server

    with pytest.raises(HTTPError) as error:
        urlopen(f"{base_url}/profile", timeout=2)

    assert error.value.code == 401


def test_profile_endpoint_reports_and_updates_profile(control_server: tuple[str, Mock]) -> None:
    base_url, service = control_server
    headers = {
        "Authorization": "Bearer secret-token",
        "Content-Type": "application/json",
    }
    with urlopen(Request(f"{base_url}/profile", headers=headers), timeout=2) as response:
        payload = json.load(response)

    assert payload == {
        "automation_status": "disabled",
        "operational_profile": "test",
        "running": False,
        "status": "test_idle",
    }

    request = Request(
        f"{base_url}/profile",
        data=b'{"operational_profile": "production"}',
        method="POST",
        headers=headers,
    )
    with urlopen(request, timeout=2) as response:
        assert response.status == 200

    service.set_operational_profile.assert_called_once_with(OperationalProfile.PRODUCTION)


def test_profile_endpoint_rejects_unknown_profile(control_server: tuple[str, Mock]) -> None:
    base_url, service = control_server
    request = Request(
        f"{base_url}/profile",
        data=b'{"operational_profile": "staging"}',
        method="POST",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "application/json",
        },
    )

    with pytest.raises(HTTPError) as error:
        urlopen(request, timeout=2)

    assert error.value.code == 400
    service.set_operational_profile.assert_not_called()
