"""Fronteira autenticada e limitada ao loopback do controlador RTD do host."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app.rtd_control_server import _handler, resolve_control_host
from app.rtd_service import OperationalProfile

TOKEN = "token-de-teste-com-tamanho-suficiente"


class _ServicoFalso:
    def __init__(self) -> None:
        self.is_running = False
        self.status = "stopped"
        self.operational_profile = OperationalProfile.TEST
        self.automation_status = "enabled"
        self.calls: list[str] = []

    def start(self) -> bool:
        self.calls.append("start")
        self.is_running = True
        self.status = "running"
        return True

    def stop(self) -> bool:
        self.calls.append("stop")
        self.is_running = False
        self.status = "stopped"
        return True

    def set_operational_profile(self, profile: OperationalProfile) -> bool:
        self.calls.append(f"profile:{profile.value}")
        self.operational_profile = profile
        return True


@pytest.fixture
def controller() -> tuple[ThreadingHTTPServer, _ServicoFalso]:
    service = _ServicoFalso()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(service, TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, service
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _request(
    server: ThreadingHTTPServer,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=data,
        headers=headers,
        method="GET" if data is None else "POST",
    )
    try:
        with urlopen(request, timeout=1) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def test_controlador_recusa_leitura_e_comando_sem_token(controller) -> None:
    server, service = controller

    status, _ = _request(server, "/state")
    assert status == 401

    status, _ = _request(server, "/state", payload={"enabled": True})
    assert status == 401
    assert service.calls == []


def test_controlador_autorizado_expoe_estado_e_muda_coletor(controller) -> None:
    server, service = controller

    status, state = _request(server, "/state", token=TOKEN)
    assert status == 200
    assert state["running"] is False
    assert state["operational_profile"] == "test"

    status, state = _request(server, "/state", token=TOKEN, payload={"enabled": True})
    assert status == 200
    assert state["running"] is True
    assert service.calls == ["start"]


def test_controlador_rejeita_comando_malformado_sem_efeito(controller) -> None:
    server, service = controller

    status, _ = _request(server, "/state", token=TOKEN, payload={"enabled": "true"})
    assert status == 400
    assert service.calls == []


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.test"])
def test_controlador_recusa_bind_fora_do_loopback(host: str) -> None:
    with pytest.raises(RuntimeError, match="loopback"):
        resolve_control_host(host)


@pytest.mark.parametrize("host", [None, "127.0.0.1", "::1"])
def test_controlador_aceita_apenas_loopbacks_suportados(host: str | None) -> None:
    assert resolve_control_host(host) == (host or "127.0.0.1")
