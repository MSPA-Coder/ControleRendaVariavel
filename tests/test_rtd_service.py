from __future__ import annotations

import subprocess
from pathlib import Path

from app import create_app, rtd_service
from app.cli import supervisor_process_is_alive
from app.rtd_service import RtdServiceManager


class _Process:
    pid = 1234

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass


def test_hidden_console_kwargs_explicitly_hides_windows_subprocesses(monkeypatch) -> None:
    class _StartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 0
            self.wShowWindow = -1

    monkeypatch.setattr(rtd_service.sys, "platform", "win32")
    monkeypatch.setattr(rtd_service.subprocess, "CREATE_NO_WINDOW", 8, raising=False)
    monkeypatch.setattr(rtd_service.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(rtd_service.subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(rtd_service.subprocess, "STARTUPINFO", _StartupInfo, raising=False)

    options = rtd_service._hidden_console_kwargs()

    startupinfo = options["startupinfo"]
    assert options["creationflags"] == 8
    assert startupinfo.dwFlags == 1
    assert startupinfo.wShowWindow == 0


def test_rtd_collector_child_is_marked_to_prevent_supervisor_recursion(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_popen(*args: object, **kwargs: object) -> _Process:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _Process()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    manager = RtdServiceManager(
        tmp_path,
        available=False,
        background_supervision=False,
    )

    assert manager._start_process_locked() is True

    environment = captured["kwargs"]["env"]
    assert isinstance(environment, dict)
    assert environment["RTD_COLLECTOR_PROCESS"] == "true"
    assert environment["RTD_SUPERVISOR_PID"].isdigit()


def test_collector_process_does_not_start_a_local_supervisor(monkeypatch) -> None:
    monkeypatch.setenv("RTD_COLLECTOR_PROCESS", "true")
    monkeypatch.delenv("RTD_CONTROL_URL", raising=False)
    monkeypatch.delenv("RTD_CONTROL_TOKEN", raising=False)

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
        }
    )

    service = app.extensions["rtd_service"]
    assert isinstance(service, RtdServiceManager)
    assert service._background_supervision is False


def test_supervision_starts_when_background_supervision_is_enabled(tmp_path: Path) -> None:
    manager = RtdServiceManager(
        tmp_path,
        available=True,
        background_supervision=False,
    )
    calls: list[bool] = []
    manager._ensure_supervisor = lambda: calls.append(True)  # type: ignore[method-assign]

    manager.enable_background_supervision()

    assert calls == [True]


def test_collector_uses_console_python_when_supervisor_uses_pythonw(
    monkeypatch, tmp_path: Path
) -> None:
    windowless = tmp_path / "pythonw.exe"
    console = tmp_path / "python.exe"
    console.write_text("", encoding="utf-8")
    monkeypatch.setattr(rtd_service.sys, "platform", "win32")
    monkeypatch.setattr(rtd_service.sys, "executable", str(windowless))

    assert rtd_service._collector_python_executable() == str(console)


def test_collector_stops_when_supervisor_process_is_gone(monkeypatch) -> None:
    def missing_process(_process_id: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr("app.cli.os.kill", missing_process)

    assert supervisor_process_is_alive(None) is True
    assert supervisor_process_is_alive("not-a-pid") is False
    assert supervisor_process_is_alive("0") is False
    assert supervisor_process_is_alive("1234") is False
