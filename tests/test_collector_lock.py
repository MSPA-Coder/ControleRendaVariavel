from __future__ import annotations

import pytest

from app.collector_lock import CollectorAlreadyRunningError, collector_process_lock


def test_collector_lock_is_exclusive_and_releases_on_exit(tmp_path) -> None:
    with (
        collector_process_lock(tmp_path),
        pytest.raises(CollectorAlreadyRunningError),
        collector_process_lock(tmp_path),
    ):
        pass

    with collector_process_lock(tmp_path):
        pass


def test_collector_lock_nonblocking_mode_reports_external_owner(tmp_path) -> None:
    with (
        collector_process_lock(tmp_path),
        pytest.raises(CollectorAlreadyRunningError),
        collector_process_lock(tmp_path, wait=False),
    ):
        pass
