from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time

from app.domain import MARKET_TIMEZONE
from app.models import AppSetting, CollectorMode
from app.themes import DEFAULT_THEME

MIN_POLL_INTERVAL_SECONDS = 1
MAX_POLL_INTERVAL_SECONDS = 3600
DEFAULT_POLL_INTERVAL_SECONDS = 2
MIN_AGENT_CHECK_INTERVAL_SECONDS = 5
MAX_AGENT_CHECK_INTERVAL_SECONDS = 3600
DEFAULT_AGENT_CHECK_INTERVAL_SECONDS = 30
DEFAULT_COLLECTOR_SCHEDULE_WEEKDAYS = "0,1,2,3,4"
DEFAULT_COLLECTOR_SCHEDULE_START_TIME = time(9, 45)
DEFAULT_COLLECTOR_SCHEDULE_END_TIME = time(18, 10)


@dataclass(frozen=True, slots=True)
class CollectorSettingsInput:
    collector_mode: CollectorMode
    poll_interval_seconds: int


def parse_collector_settings(form: Mapping[str, str]) -> CollectorSettingsInput:
    try:
        collector_mode = CollectorMode(form.get("collector_mode", ""))
        poll_interval_seconds = int(form.get("poll_interval_seconds", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("Selecione um coletor e informe um intervalo válido.") from exc
    if not MIN_POLL_INTERVAL_SECONDS <= poll_interval_seconds <= MAX_POLL_INTERVAL_SECONDS:
        raise ValueError(
            f"O intervalo deve ficar entre {MIN_POLL_INTERVAL_SECONDS} e "
            f"{MAX_POLL_INTERVAL_SECONDS} segundos."
        )
    return CollectorSettingsInput(collector_mode, poll_interval_seconds)


def parse_agent_check_interval(form: Mapping[str, str]) -> int:
    try:
        value = int(form.get("agent_check_interval_seconds", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("Informe um intervalo válido para a verificação do agente.") from exc
    if not MIN_AGENT_CHECK_INTERVAL_SECONDS <= value <= MAX_AGENT_CHECK_INTERVAL_SECONDS:
        raise ValueError(
            "O intervalo de verificação do agente deve ficar entre "
            f"{MIN_AGENT_CHECK_INTERVAL_SECONDS} e {MAX_AGENT_CHECK_INTERVAL_SECONDS} segundos."
        )
    return value


def parse_collector_schedule_weekdays(values: list[str]) -> str:
    try:
        weekdays = sorted({int(value) for value in values})
    except (TypeError, ValueError) as exc:
        raise ValueError("Selecione os dias da semana para a coleta.") from exc
    if not weekdays or any(day < 0 or day > 6 for day in weekdays):
        raise ValueError("Selecione ao menos um dia válido para a coleta.")
    return ",".join(str(day) for day in weekdays)


def parse_collector_schedule_time(value: str, *, field: str) -> time:
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Informe um horário válido para {field}.") from exc


def parse_collector_schedule(form: Mapping[str, str], weekdays: list[str]) -> tuple[str, time, time]:
    start_time = parse_collector_schedule_time(
        form.get("collector_schedule_start_time", ""), field="o início da coleta"
    )
    end_time = parse_collector_schedule_time(
        form.get("collector_schedule_end_time", ""), field="o fim da coleta"
    )
    if start_time >= end_time:
        raise ValueError("O horário de início da coleta deve ser anterior ao horário final.")
    return parse_collector_schedule_weekdays(weekdays), start_time, end_time


def collector_schedule_is_active(
    weekdays: str,
    start_time: time,
    end_time: time,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a collector may run in the configured B3-time window."""
    try:
        configured_days = {int(value) for value in weekdays.split(",")}
    except ValueError:
        return False
    local_now = (now or datetime.now(MARKET_TIMEZONE)).astimezone(MARKET_TIMEZONE)
    return (
        local_now.weekday() in configured_days
        and start_time <= local_now.timetz().replace(tzinfo=None) < end_time
    )


def default_collector_settings() -> AppSetting:
    return AppSetting(
        id=1,
        theme=DEFAULT_THEME,
        collector_mode=CollectorMode.EXCEL,
        poll_interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
        agent_check_interval_seconds=DEFAULT_AGENT_CHECK_INTERVAL_SECONDS,
        collector_schedule_weekdays=DEFAULT_COLLECTOR_SCHEDULE_WEEKDAYS,
        collector_schedule_start_time=DEFAULT_COLLECTOR_SCHEDULE_START_TIME,
        collector_schedule_end_time=DEFAULT_COLLECTOR_SCHEDULE_END_TIME,
    )
