from __future__ import annotations

from datetime import UTC, datetime, time

from app.collector.settings import schedule_from_settings
from app.models import AppSetting


def _agenda_da_b3() -> AppSetting:
    return AppSetting(
        collector_schedule_weekdays="0,1,2,3,4",
        collector_schedule_start_time=time(9, 45),
        collector_schedule_end_time=time(18, 10),
    )


def test_agenda_do_coletor_local_usa_horario_da_b3_para_todos_os_ativos() -> None:
    agenda = schedule_from_settings(_agenda_da_b3())

    assert agenda.is_active(datetime(2026, 8, 17, 12, 45, tzinfo=UTC))
    assert not agenda.is_active(datetime(2026, 8, 17, 21, 10, tzinfo=UTC))
