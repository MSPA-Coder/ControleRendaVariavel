from __future__ import annotations

import subprocess
from pathlib import Path

from app import create_app
from app.rtd_service import OperationalProfileStore, RtdServiceManager


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
        profile_store=OperationalProfileStore(tmp_path / "profile"),
    )

    assert manager._start_process_locked() is True

    environment = captured["kwargs"]["env"]
    assert isinstance(environment, dict)
    assert environment["RTD_COLLECTOR_PROCESS"] == "true"


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
