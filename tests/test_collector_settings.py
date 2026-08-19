from __future__ import annotations

from datetime import UTC, datetime, time

from app.collector_settings import collector_schedule_is_active


def test_agenda_do_coletor_local_usa_horario_da_b3_para_todos_os_ativos() -> None:
    assert collector_schedule_is_active(
        "0,1,2,3,4",
        time(9, 45),
        time(18, 10),
        now=datetime(2026, 8, 17, 12, 45, tzinfo=UTC),
    )
    assert not collector_schedule_is_active(
        "0,1,2,3,4",
        time(9, 45),
        time(18, 10),
        now=datetime(2026, 8, 17, 21, 10, tzinfo=UTC),
    )
