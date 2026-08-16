from __future__ import annotations

import hmac
import json
import os
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.host_bootstrap import compose_up, resolve_docker_cli, wait_for_docker
from app.host_env import apply_host_environment
from app.rtd_service import OperationalProfile, RtdServiceManager

MAX_BODY_BYTES = 1024
MIN_TOKEN_LENGTH = 32
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


def resolve_control_host(configured: str | None) -> str:
    """Aceita somente um bind local para o controlador privilegiado do host."""
    host = (configured or "127.0.0.1").strip()
    if host not in LOOPBACK_HOSTS:
        raise RuntimeError("RTD_CONTROL_HOST deve ser um endereço de loopback.")
    return host


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


def read_or_create_token(path: Path) -> str:
    """Token persistido do controlador, gerado uma única vez por instalação.

    Sobrevive a reinícios: a tarefa agendada relança este processo a cada
    logon, e um token novo a cada vez invalidaria o segredo que o `web` do
    Compose monta, exigindo recriar o contêiner.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
    else:
        token = secrets.token_hex(32)
        path.write_text(token, encoding="utf-8")
    if len(token) < MIN_TOKEN_LENGTH:
        raise RuntimeError(f"Token do controlador RTD inválido em {path}.")
    return token


def _bootstrap(project_dir: Path) -> str:
    """Liga o Docker e a pilha, e devolve o token pronto no ambiente do processo.

    Ordem importa: o token precisa estar em `.secrets` ANTES do `compose up`,
    para que o `web` o receba como segredo montado e já suba com o
    `Authorization` que este processo vai exigir; `apply_host_environment` só
    entra depois, porque só o coletor (subprocesso `flask poll-rtd`, iniciado
    sob demanda por `RtdServiceManager`) precisa de DATABASE_URL/SECRET_KEY.
    """
    secrets_dir = project_dir / ".secrets"
    docker_cli = resolve_docker_cli()
    wait_for_docker(docker_cli)
    token = read_or_create_token(secrets_dir / "rtd_control_token")
    compose_up(docker_cli)
    apply_host_environment(project_dir)
    return token


def main() -> None:
    if os.name != "nt":
        raise SystemExit("O controlador RTD deve ser executado no Windows.")
    project_dir = Path(__file__).resolve().parent.parent
    token = _bootstrap(project_dir)

    host = resolve_control_host(os.getenv("RTD_CONTROL_HOST"))
    port = int(os.getenv("RTD_CONTROL_PORT", "8765"))
    service = RtdServiceManager(project_dir, available=True)
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
