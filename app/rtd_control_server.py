from __future__ import annotations

import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.rtd_service import OperationalProfile, RtdServiceManager

MAX_BODY_BYTES = 1024


def _handler(
    service: RtdServiceManager, token: str
) -> type[BaseHTTPRequestHandler]:
    class RtdControlHandler(BaseHTTPRequestHandler):
        def _authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            return hmac.compare_digest(supplied, f"Bearer {token}")

        def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _reject_unauthorized(self) -> None:
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "Não autorizado."})

        def do_GET(self) -> None:
            if self.path not in {"/state", "/profile"}:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "Rota inexistente."})
                return
            if not self._authorized():
                self._reject_unauthorized()
                return
            if self.path == "/profile":
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "operational_profile": service.operational_profile.value,
                        "running": service.is_running,
                        "status": service.status,
                        "automation_status": service.automation_status,
                    },
                )
                return
            self._write_json(
                HTTPStatus.OK,
                {
                    "running": service.is_running,
                    "status": service.status,
                    "operational_profile": service.operational_profile.value,
                    "automation_status": service.automation_status,
                },
            )

        def do_POST(self) -> None:
            if self.path not in {"/state", "/profile"}:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "Rota inexistente."})
                return
            if not self._authorized():
                self._reject_unauthorized()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY_BYTES:
                    raise ValueError
                payload = json.loads(self.rfile.read(length))
                if self.path == "/profile":
                    raw_profile = payload.get("operational_profile")
                    if not isinstance(raw_profile, str):
                        raise ValueError
                    service.set_operational_profile(OperationalProfile(raw_profile))
                else:
                    enabled = payload.get("enabled")
                    if not isinstance(enabled, bool):
                        raise ValueError
                    service.start() if enabled else service.stop()
            except (json.JSONDecodeError, ValueError):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": (
                            "Informe 'operational_profile' como 'test' ou 'production'."
                            if self.path == "/profile"
                            else "Informe o estado booleano 'enabled'."
                        )
                    },
                )
                return
            except (OSError, RuntimeError) as exc:
                self._write_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {
                        "error": str(exc),
                        "running": service.is_running,
                        "status": service.status,
                    },
                )
                return
            self._write_json(
                HTTPStatus.OK,
                {
                    "running": service.is_running,
                    "status": service.status,
                    "operational_profile": service.operational_profile.value,
                    "automation_status": service.automation_status,
                },
            )

        def log_message(self, format: str, *args: object) -> None:
            return

    return RtdControlHandler


def main() -> None:
    if os.name != "nt":
        raise SystemExit("O controlador RTD deve ser executado no Windows.")
    token = os.getenv("RTD_CONTROL_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("Defina RTD_CONTROL_TOKEN com pelo menos 32 caracteres.")

    host = os.getenv("RTD_CONTROL_HOST", "127.0.0.1")
    port = int(os.getenv("RTD_CONTROL_PORT", "8765"))
    service = RtdServiceManager(Path(__file__).resolve().parent.parent, available=True)
    server = ThreadingHTTPServer((host, port), _handler(service, token))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
        server.server_close()


if __name__ == "__main__":
    main()
