from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class Process(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


class RtdService(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def is_running(self) -> bool: ...

    def start(self) -> bool: ...

    def stop(self) -> bool: ...


class RtdServiceManager:
    """Owns the RTD collector process started by this web application."""

    def __init__(self, project_dir: Path, *, available: bool = sys.platform == "win32") -> None:
        self.project_dir = project_dir
        self.available = available
        self._process: Process | None = None
        self._external_process_ids: set[int] = set()
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        with self._lock:
            if not self.available:
                raise RuntimeError("O coletor RTD só pode ser iniciado no Windows.")
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
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            return True

    def stop(self) -> bool:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._process = None
                return False

            process = self._process
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            finally:
                self._process = None
                self._stop_new_rtd_processes()
            return True

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

    def _request(self, enabled: bool | None = None) -> dict[str, object]:
        data = None if enabled is None else json.dumps({"enabled": enabled}).encode()
        request = Request(
            f"{self.base_url}/state",
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
        except (HTTPError, URLError, OSError, ValueError) as exc:
            raise RuntimeError("Controlador RTD do Windows indisponível.") from exc

    @property
    def available(self) -> bool:
        try:
            self._request()
        except RuntimeError:
            return False
        return True

    @property
    def is_running(self) -> bool:
        return bool(self._request().get("running"))

    def start(self) -> bool:
        was_running = self.is_running
        self._request(True)
        return not was_running

    def stop(self) -> bool:
        was_running = self.is_running
        self._request(False)
        return was_running
