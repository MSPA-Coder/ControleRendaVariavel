from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OperationalProfile(StrEnum):
    TEST = "test"
    PRODUCTION = "production"


class Process(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


class ProfitDetector(Protocol):
    def is_running(self) -> bool: ...


class AutomationController(Protocol):
    """Leitura da tarefa agendada do host — não escreve nela.

    A tarefa fica sempre habilitada nos dois perfis (ver
    ``scripts/rtd-host.ps1``); só decide se o coletor auto-inicia
    supervisionado. `status()` alimenta ``automation_status`` para a tela,
    sem influenciar `start`/`stop`/`set_operational_profile`.
    """

    def status(self) -> str: ...


class RtdService(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def status(self) -> str: ...

    @property
    def operational_profile(self) -> OperationalProfile: ...

    def set_operational_profile(self, profile: OperationalProfile) -> bool: ...

    def start(self) -> bool: ...

    def stop(self) -> bool: ...


class OperationalProfileStore:
    """Host-owned profile that is readable before Docker or PostgreSQL starts."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> OperationalProfile:
        try:
            value = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return OperationalProfile.TEST
        try:
            return OperationalProfile(value)
        except ValueError as exc:
            raise RuntimeError(f"Perfil operacional inválido em {self.path}.") from exc

    def write(self, profile: OperationalProfile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(f"{profile.value}\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


class PowerShellAutomationController:
    def __init__(self, project_dir: Path, script: Path | None = None) -> None:
        configured = os.getenv("RTD_AUTOMATION_SCRIPT", "scripts/rtd-host.ps1")
        selected = script or Path(configured)
        self.script = selected if selected.is_absolute() else project_dir / selected

    def _run(self, action: str) -> str:
        if not self.script.is_file():
            raise RuntimeError(f"Script de automação RTD não encontrado: {self.script}")
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self.script),
                    "-Action",
                    action,
                ],
                check=True,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", "") or ""
            suffix = f" ({detail.strip()})" if detail.strip() else ""
            raise RuntimeError(f"Falha ao configurar a automação RTD{suffix}") from exc
        return result.stdout.strip().lower()

    def status(self) -> str:
        value = self._run("Status")
        return value if value in {"enabled", "disabled", "absent"} else "unknown"


class WindowsProfitDetector:
    """Detects the interactive Profit process without attempting COM activation."""

    def is_running(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "$items = Get-CimInstance Win32_Process -Filter "
                    "\"Name = 'ProfitChart.exe'\" -ErrorAction SilentlyContinue | "
                    "Where-Object { $_.CommandLine -notmatch "
                    "'(?:^|\\s)-Embedding(?:\\s|$)' }; @($items).Count",
                ],
                check=True,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                text=True,
                timeout=5,
            )
            return int(result.stdout.strip() or "0") > 0
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise RuntimeError("Não foi possível verificar o processo do Profit.") from exc


class RtdServiceManager:
    """Owns and, in production, supervises the Windows RTD collector process."""

    def __init__(
        self,
        project_dir: Path,
        *,
        available: bool = sys.platform == "win32",
        profile_store: OperationalProfileStore | None = None,
        automation: AutomationController | None = None,
        profit_detector: ProfitDetector | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        supervisor_interval_seconds: float = 2,
        profit_stability_seconds: float = 10,
        restart_backoff_seconds: tuple[float, ...] = (5, 15, 30, 60),
        restart_reset_seconds: float = 60,
        background_supervision: bool = True,
    ) -> None:
        self.project_dir = project_dir
        self.available = available
        self._profile_store = profile_store or OperationalProfileStore(
            project_dir / ".docker-local" / "operational-profile"
        )
        self._automation = automation or PowerShellAutomationController(project_dir)
        self._profit_detector = profit_detector or WindowsProfitDetector()
        self._monotonic = monotonic
        self._supervisor_interval_seconds = supervisor_interval_seconds
        self._profit_stability_seconds = profit_stability_seconds
        self._restart_backoff_seconds = restart_backoff_seconds
        self._restart_reset_seconds = restart_reset_seconds
        self._background_supervision = background_supervision
        self._process: Process | None = None
        self._external_process_ids: set[int] = set()
        self._lock = threading.RLock()
        self._wake_supervisor = threading.Event()
        self._shutdown_supervisor = threading.Event()
        self._supervisor_thread: threading.Thread | None = None
        self._profile = self._profile_store.read()
        self._desired_running = self._profile == OperationalProfile.PRODUCTION
        self._profit_seen_at: float | None = None
        self._next_start_at = 0.0
        self._failure_count = 0
        self._process_started_at: float | None = None
        self._automation_status = "unknown"
        self._status = (
            "waiting_for_profit"
            if self._profile == OperationalProfile.PRODUCTION
            else "test_idle"
        )
        if self.available:
            try:
                self._automation_status = self._automation.status()
            except (OSError, RuntimeError):
                # The controller is already alive, so keep serving state and
                # serving this session even when Task Scheduler is briefly
                # unavailable. A later controller restart can reconcile it.
                self._automation_status = "error"
        if self._profile == OperationalProfile.PRODUCTION and self.available:
            self._ensure_supervisor()

    @property
    def operational_profile(self) -> OperationalProfile:
        with self._lock:
            return self._profile

    @property
    def status(self) -> str:
        with self._lock:
            if not self.available:
                return "unavailable"
            return self._status

    @property
    def automation_status(self) -> str:
        with self._lock:
            return self._automation_status

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def set_operational_profile(self, profile: OperationalProfile) -> bool:
        profile = OperationalProfile(profile)
        with self._lock:
            if profile == self._profile:
                return False

            if profile == OperationalProfile.PRODUCTION:
                self._profile_store.write(profile)
                self._profile = profile
                self._desired_running = True
                self._status = "waiting_for_profit"
                self._profit_seen_at = None
                self._failure_count = 0
                self._next_start_at = 0
                self._ensure_supervisor()
                self._wake_supervisor.set()
                return True

            self._desired_running = False
            self._stop_process_locked()
            self._profile_store.write(profile)
            self._profile = profile
            self._status = "test_idle"
            self._profit_seen_at = None
            self._wake_supervisor.set()
            return True

    def start(self) -> bool:
        with self._lock:
            if not self.available:
                raise RuntimeError("O coletor RTD só pode ser iniciado no Windows.")
            self._desired_running = True
            if self._profile == OperationalProfile.PRODUCTION:
                self._ensure_supervisor()
                self._wake_supervisor.set()
                return self._supervise_once_locked(self._monotonic())
            return self._start_process_locked()

    def stop(self) -> bool:
        with self._lock:
            self._desired_running = False
            stopped = self._stop_process_locked()
            self._status = (
                "test_idle"
                if self._profile == OperationalProfile.TEST
                else "production_stopped"
            )
            self._wake_supervisor.set()
            return stopped

    def close(self) -> None:
        self._shutdown_supervisor.set()
        self._wake_supervisor.set()
        with self._lock:
            self._desired_running = False
            self._stop_process_locked()
        thread = self._supervisor_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def supervise_once(self) -> bool:
        """Run one deterministic supervisor cycle; public to support focused tests."""
        with self._lock:
            return self._supervise_once_locked(self._monotonic())

    def _supervise_once_locked(self, now: float) -> bool:
        if self._profile != OperationalProfile.PRODUCTION or not self._desired_running:
            return False

        profit_running = self._profit_detector.is_running()
        if not profit_running:
            self._profit_seen_at = None
            self._stop_process_locked()
            self._status = "waiting_for_profit"
            self._failure_count = 0
            self._next_start_at = 0
            return False

        if self._profit_seen_at is None:
            self._profit_seen_at = now
        if now - self._profit_seen_at < self._profit_stability_seconds:
            self._status = "starting"
            return False

        if self._process is not None and self._process.poll() is None:
            self._status = "running"
            if (
                self._process_started_at is not None
                and now - self._process_started_at >= self._restart_reset_seconds
            ):
                self._failure_count = 0
            return False

        if self._process is not None:
            self._stop_new_rtd_processes()
            self._process = None
            self._process_started_at = None
            delay = self._restart_backoff_seconds[
                min(self._failure_count, len(self._restart_backoff_seconds) - 1)
            ]
            self._failure_count += 1
            self._next_start_at = max(self._next_start_at, now + delay)
        if now < self._next_start_at:
            self._status = "backoff"
            return False

        self._status = "starting"
        return self._start_process_locked()

    def _ensure_supervisor(self) -> None:
        if not self._background_supervision:
            return
        if self._supervisor_thread is not None and self._supervisor_thread.is_alive():
            return
        self._shutdown_supervisor.clear()
        self._supervisor_thread = threading.Thread(
            target=self._supervisor_loop,
            name="rtd-collector-supervisor",
            daemon=True,
        )
        self._supervisor_thread.start()

    def _supervisor_loop(self) -> None:
        while not self._shutdown_supervisor.is_set():
            try:
                self.supervise_once()
            except (OSError, RuntimeError):
                with self._lock:
                    self._status = "error"
                    delay = self._restart_backoff_seconds[
                        min(self._failure_count, len(self._restart_backoff_seconds) - 1)
                    ]
                    self._failure_count += 1
                    self._next_start_at = self._monotonic() + delay
            self._wake_supervisor.wait(self._supervisor_interval_seconds)
            self._wake_supervisor.clear()

    def _start_process_locked(self) -> bool:
        if self._process is not None and self._process.poll() is None:
            return False
        self._external_process_ids = self._rtd_process_ids()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "flask",
                "--app",
                "app:create_app",
                "poll-rtd",
                "--watch",
            ],
            cwd=self.project_dir,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._process_started_at = self._monotonic()
        self._status = "running"
        return True

    def _stop_process_locked(self) -> bool:
        if self._process is None or self._process.poll() is not None:
            self._process = None
            return False
        process = self._process
        if sys.platform == "win32":
            self._terminate_windows_process_tree(process.pid, force=False)
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                self._terminate_windows_process_tree(process.pid, force=True)
            else:
                process.kill()
            process.wait(timeout=5)
        finally:
            self._process = None
            self._process_started_at = None
            self._stop_new_rtd_processes()
        return True

    @staticmethod
    def _terminate_windows_process_tree(process_id: int, *, force: bool) -> None:
        command = ["taskkill.exe", "/PID", str(process_id), "/T"]
        if force:
            command.append("/F")
        subprocess.run(
            command,
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=5,
        )

    @staticmethod
    def _rtd_process_ids() -> set[int]:
        if sys.platform != "win32":
            return set()
        command = (
            "$items = Get-CimInstance Win32_Process | Where-Object { "
            "($_.Name -eq 'EXCEL.EXE' -and $_.CommandLine -match "
            "'/automation\\s+-Embedding') -or "
            "($_.Name -eq 'profitchart.exe' -and $_.CommandLine -match "
            "'(?:^|\\s)-Embedding(?:\\s|$)') }; "
            "@($items.ProcessId) | ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                check=True,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                text=True,
                timeout=5,
            )
            values = json.loads(result.stdout or "[]")
        except (OSError, subprocess.SubprocessError, ValueError):
            return set()
        if isinstance(values, int):
            return {values}
        return {int(value) for value in values}

    def _stop_new_rtd_processes(self) -> None:
        process_ids = self._rtd_process_ids() - self._external_process_ids
        self._external_process_ids = set()
        if not process_ids:
            return
        ids = ",".join(str(process_id) for process_id in sorted(process_ids))
        # Cleanup of COM processes is defensive and must not prevent the
        # tracked collector from reaching a stopped state.
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    f"Stop-Process -Id {ids} -Force -ErrorAction SilentlyContinue",
                ],
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=5,
            )


class RemoteRtdService:
    """Controls the Windows RTD collector through the authenticated host helper."""

    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 2) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(self, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method="GET" if data is None else "POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return cast(dict[str, object], json.load(response))
        except HTTPError as exc:
            try:
                error_payload = json.load(exc)
                message = str(error_payload.get("error", ""))
            except (AttributeError, OSError, ValueError):
                message = ""
            raise RuntimeError(message or "Controlador RTD do Windows indisponível.") from exc
        except (URLError, OSError, ValueError) as exc:
            raise RuntimeError("Controlador RTD do Windows indisponível.") from exc

    @property
    def available(self) -> bool:
        try:
            self._request("/state")
        except RuntimeError:
            return False
        return True

    @property
    def is_running(self) -> bool:
        return bool(self._request("/state").get("running"))

    @property
    def status(self) -> str:
        return str(self._request("/state").get("status", "unavailable"))

    @property
    def operational_profile(self) -> OperationalProfile:
        payload = self._request("/profile")
        try:
            return OperationalProfile(str(payload["operational_profile"]))
        except (KeyError, ValueError) as exc:
            raise RuntimeError("Controlador RTD retornou perfil inválido.") from exc

    def set_operational_profile(self, profile: OperationalProfile) -> bool:
        current = self.operational_profile
        if current == profile:
            return False
        self._request("/profile", {"operational_profile": profile.value})
        return True

    def start(self) -> bool:
        was_running = self.is_running
        self._request("/state", {"enabled": True})
        return not was_running

    def stop(self) -> bool:
        was_running = self.is_running
        self._request("/state", {"enabled": False})
        return was_running
