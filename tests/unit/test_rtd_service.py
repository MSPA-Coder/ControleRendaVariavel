import subprocess
from pathlib import Path
from unittest.mock import Mock, call, patch

import pytest

from app.rtd_service import (
    OperationalProfile,
    OperationalProfileStore,
    RemoteRtdService,
    RtdServiceManager,
)

pytestmark = [pytest.mark.critical]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


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


def test_operational_profile_store_defaults_to_test_and_writes_atomically(tmp_path: Path) -> None:
    path = tmp_path / ".docker-local" / "operational-profile"
    store = OperationalProfileStore(path)

    assert store.read() == OperationalProfile.TEST
    store.write(OperationalProfile.PRODUCTION)

    assert path.read_text(encoding="utf-8") == "production\n"
    assert store.read() == OperationalProfile.PRODUCTION
    assert list(path.parent.glob("*.tmp")) == []


def test_test_profile_disables_drifted_automation_on_controller_start(
    tmp_path: Path,
) -> None:
    automation = Mock()
    automation.status.return_value = "enabled"

    manager = RtdServiceManager(
        tmp_path,
        available=True,
        automation=automation,
        background_supervision=False,
    )

    automation.set_enabled.assert_called_once_with(False)
    assert manager.automation_status == "disabled"
    assert manager.status == "test_idle"


def test_production_waits_for_stable_profit_before_starting(tmp_path: Path) -> None:
    store = OperationalProfileStore(tmp_path / ".docker-local" / "operational-profile")
    store.write(OperationalProfile.PRODUCTION)
    automation = Mock()
    detector = Mock()
    detector.is_running.return_value = False
    clock = Clock()
    process = Mock()
    process.poll.return_value = None
    manager = RtdServiceManager(
        tmp_path,
        available=True,
        profile_store=store,
        automation=automation,
        profit_detector=detector,
        monotonic=clock,
        profit_stability_seconds=10,
        background_supervision=False,
    )

    with (
        patch.object(manager, "_rtd_process_ids", return_value=set()),
        patch("app.rtd_service.subprocess.Popen", return_value=process) as popen,
    ):
        assert manager.supervise_once() is False
        assert manager.status == "waiting_for_profit"
        detector.is_running.return_value = True
        assert manager.supervise_once() is False
        assert manager.status == "starting"
        clock.now = 9
        assert manager.supervise_once() is False
        clock.now = 10
        assert manager.supervise_once() is True

    popen.assert_called_once()
    assert manager.status == "running"
    automation.set_enabled.assert_called_once_with(True)


def test_production_stops_collector_when_profit_disappears(tmp_path: Path) -> None:
    store = OperationalProfileStore(tmp_path / "profile")
    store.write(OperationalProfile.PRODUCTION)
    detector = Mock()
    detector.is_running.return_value = False
    process = Mock()
    process.poll.return_value = None
    manager = RtdServiceManager(
        tmp_path,
        available=True,
        profile_store=store,
        automation=Mock(),
        profit_detector=detector,
        background_supervision=False,
    )
    manager._process = process

    with patch.object(manager, "_rtd_process_ids", return_value=set()):
        manager.supervise_once()

    process.terminate.assert_called_once_with()
    assert manager.status == "waiting_for_profit"
    assert manager.is_running is False


def test_crashed_collector_uses_bounded_restart_backoff(tmp_path: Path) -> None:
    store = OperationalProfileStore(tmp_path / "profile")
    store.write(OperationalProfile.PRODUCTION)
    detector = Mock()
    detector.is_running.return_value = True
    clock = Clock()
    crashed = Mock()
    crashed.poll.return_value = 1
    manager = RtdServiceManager(
        tmp_path,
        available=True,
        profile_store=store,
        automation=Mock(),
        profit_detector=detector,
        monotonic=clock,
        profit_stability_seconds=0,
        restart_backoff_seconds=(5, 15),
        background_supervision=False,
    )
    manager._process = crashed

    assert manager.supervise_once() is False
    assert manager.status == "backoff"
    clock.now = 4
    assert manager.supervise_once() is False

    running = Mock()
    running.poll.return_value = None
    clock.now = 5
    with (
        patch.object(manager, "_rtd_process_ids", return_value=set()),
        patch("app.rtd_service.subprocess.Popen", return_value=running),
    ):
        assert manager.supervise_once() is True


def test_switch_to_production_rolls_back_profile_when_enable_fails(tmp_path: Path) -> None:
    store = OperationalProfileStore(tmp_path / "profile")
    automation = Mock()
    automation.set_enabled.side_effect = [RuntimeError("scheduler unavailable"), None]
    manager = RtdServiceManager(
        tmp_path,
        available=True,
        profile_store=store,
        automation=automation,
        background_supervision=False,
    )

    with pytest.raises(RuntimeError, match="scheduler unavailable"):
        manager.set_operational_profile(OperationalProfile.PRODUCTION)

    assert store.read() == OperationalProfile.TEST
    assert manager.operational_profile == OperationalProfile.TEST
    assert automation.set_enabled.call_args_list == [call(True), call(False)]


def test_switch_to_test_stops_collector_and_compensates_disable_failure(
    tmp_path: Path,
) -> None:
    store = OperationalProfileStore(tmp_path / "profile")
    store.write(OperationalProfile.PRODUCTION)
    automation = Mock()
    # Startup reconciliation succeeds; Disable fails; compensating Enable succeeds.
    automation.set_enabled.side_effect = [None, RuntimeError("disable failed"), None]
    manager = RtdServiceManager(
        tmp_path,
        available=True,
        profile_store=store,
        automation=automation,
        background_supervision=False,
    )
    process = Mock()
    process.poll.return_value = None
    manager._process = process

    with (
        patch.object(manager, "_rtd_process_ids", return_value=set()),
        pytest.raises(RuntimeError, match="disable failed"),
    ):
        manager.set_operational_profile(OperationalProfile.TEST)

    process.terminate.assert_called_once_with()
    assert store.read() == OperationalProfile.PRODUCTION
    assert manager.operational_profile == OperationalProfile.PRODUCTION
    assert automation.set_enabled.call_args_list[-2:] == [call(False), call(True)]


def test_remote_service_reads_and_updates_profile() -> None:
    test_response = Mock()
    test_response.__enter__ = Mock(return_value=test_response)
    test_response.__exit__ = Mock(return_value=False)
    test_response.read.return_value = b'{"operational_profile": "test"}'
    production_response = Mock()
    production_response.__enter__ = Mock(return_value=production_response)
    production_response.__exit__ = Mock(return_value=False)
    production_response.read.return_value = b'{"operational_profile": "production"}'
    service = RemoteRtdService("http://host:8765", "secret-token")

    with patch(
        "app.rtd_service.urlopen",
        side_effect=[test_response, production_response],
    ) as request:
        assert service.set_operational_profile(OperationalProfile.PRODUCTION) is True

    update = request.call_args_list[1].args[0]
    assert update.full_url == "http://host:8765/profile"
    assert update.data == b'{"operational_profile": "production"}'


def test_windows_stop_terminates_exact_tracked_process_tree(tmp_path: Path) -> None:
    process = Mock()
    process.pid = 4321
    process.poll.return_value = None
    manager = RtdServiceManager(tmp_path, available=True)
    manager._process = process

    with (
        patch("app.rtd_service.sys.platform", "win32"),
        patch.object(manager, "_rtd_process_ids", return_value=set()),
        patch("app.rtd_service.subprocess.run") as run,
    ):
        assert manager.stop() is True

    assert run.call_args_list[0].args[0] == ["taskkill.exe", "/PID", "4321", "/T"]
    process.terminate.assert_not_called()


def test_windows_stop_forces_same_tree_after_graceful_timeout(tmp_path: Path) -> None:
    process = Mock()
    process.pid = 9876
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("taskkill", 5), 0]
    manager = RtdServiceManager(tmp_path, available=True)
    manager._process = process

    with (
        patch("app.rtd_service.sys.platform", "win32"),
        patch.object(manager, "_rtd_process_ids", return_value=set()),
        patch("app.rtd_service.subprocess.run") as run,
    ):
        assert manager.stop() is True

    assert run.call_args_list[1].args[0] == [
        "taskkill.exe",
        "/PID",
        "9876",
        "/T",
        "/F",
    ]
