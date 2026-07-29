from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app import create_app
from app.rtd_service import RemoteRtdService, RtdServiceManager


def test_start_is_idempotent_and_uses_collector_command() -> None:
    process = Mock()
    process.poll.return_value = None
    manager = RtdServiceManager(Path("C:/project"), available=True)

    with (
        patch.object(manager, "_rtd_process_ids", return_value=set()),
        patch("app.rtd_service.subprocess.Popen", return_value=process) as popen,
    ):
        assert manager.start() is True
        assert manager.start() is False

    assert popen.call_count == 1
    command = popen.call_args.args[0]
    assert command[-2:] == ["poll-rtd", "--watch"]
    assert popen.call_args.kwargs["cwd"] == Path("C:/project")


def test_stop_terminates_running_collector() -> None:
    process = Mock()
    process.poll.return_value = None
    manager = RtdServiceManager(Path("C:/project"), available=True)
    manager._process = process

    with patch.object(manager, "_rtd_process_ids", return_value=set()):
        assert manager.stop() is True
    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    assert manager.is_running is False


def test_stop_removes_only_com_processes_created_after_start() -> None:
    process = Mock()
    process.poll.return_value = None
    manager = RtdServiceManager(Path("C:/project"), available=True)
    manager._process = process
    manager._external_process_ids = {10, 20}

    with (
        patch.object(manager, "_rtd_process_ids", return_value={10, 20, 30, 40}),
        patch("app.rtd_service.subprocess.run") as run,
    ):
        assert manager.stop() is True

    cleanup_command = run.call_args.args[0]
    assert cleanup_command[-1] == (
        "Stop-Process -Id 30,40 -Force -ErrorAction SilentlyContinue"
    )


def test_start_rejects_unavailable_platform() -> None:
    manager = RtdServiceManager(Path("C:/project"), available=False)

    with pytest.raises(RuntimeError, match="Windows"):
        manager.start()


def test_remote_service_sends_authenticated_start_request() -> None:
    stopped = Mock()
    stopped.__enter__ = Mock(return_value=stopped)
    stopped.__exit__ = Mock(return_value=False)
    stopped.read.return_value = b'{"running": false}'
    started = Mock()
    started.__enter__ = Mock(return_value=started)
    started.__exit__ = Mock(return_value=False)
    started.read.return_value = b'{"running": true}'
    service = RemoteRtdService("http://host:8765", "secret-token")

    with patch("app.rtd_service.urlopen", side_effect=[stopped, started]) as request:
        assert service.start() is True

    assert request.call_count == 2
    start_request = request.call_args_list[1].args[0]
    assert start_request.full_url == "http://host:8765/state"
    assert start_request.get_header("Authorization") == "Bearer secret-token"
    assert start_request.data == b'{"enabled": true}'


def test_rtd_service_api_reports_unavailable_controller_without_500() -> None:
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
    service = Mock()
    type(service).is_running = property(
        lambda _service: (_ for _ in ()).throw(RuntimeError("indisponível"))
    )
    app.extensions["rtd_service"] = service

    response = app.test_client().get("/api/rtd-service")

    assert response.status_code == 503
    assert response.get_json() == {
        "available": False,
        "error": "indisponível",
        "running": False,
    }
