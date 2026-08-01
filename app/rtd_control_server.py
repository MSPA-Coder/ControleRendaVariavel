from __future__ import annotations

import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.rtd_service import RtdServiceManager

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
            if self.path != "/state":
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "Rota inexistente."})
                return
            if not self._authorized():
                self._reject_unauthorized()
                return
            self._write_json(HTTPStatus.OK, {"running": service.is_running})

        def do_POST(self) -> None:
            if self.path != "/state":
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
                enabled = payload.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError
                service.start() if enabled else service.stop()
            except (json.JSONDecodeError, ValueError):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Informe o estado booleano 'enabled'."},
                )
                return
            except (OSError, RuntimeError) as exc:
                self._write_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": str(exc), "running": service.is_running},
                )
                return
            self._write_json(HTTPStatus.OK, {"running": service.is_running})

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
        service.stop()
        server.server_close()


if __name__ == "__main__":
    main()
