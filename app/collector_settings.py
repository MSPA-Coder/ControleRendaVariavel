from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time

from app.domain import MARKET_TIMEZONE
from app.models import AppSetting, CollectorDestination, CollectorMode
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


@dataclass(frozen=True, slots=True)
class CollectorSchedule:
    """Janela única de coleta, no fuso da B3, para qualquer destino.

    O agente Windows e o coletor que grava direto no banco enxergam a mesma
    agenda. Ter uma implementação só é o que impede os dois lados divergirem
    silenciosamente sobre quando o mercado está aberto.
    """

    weekdays: frozenset[int]
    start_time: time
    end_time: time

    def is_active(self, now: datetime | None = None) -> bool:
        local_now = (now or datetime.now(MARKET_TIMEZONE)).astimezone(MARKET_TIMEZONE)
        return (
            local_now.weekday() in self.weekdays
            and self.start_time <= local_now.timetz().replace(tzinfo=None) < self.end_time
        )

    def as_payload(self) -> dict[str, object]:
        return {
            "weekdays": sorted(self.weekdays),
            "start_time": self.start_time.strftime("%H:%M"),
            "end_time": self.end_time.strftime("%H:%M"),
        }


DEFAULT_COLLECTOR_SCHEDULE = CollectorSchedule(
    frozenset({0, 1, 2, 3, 4}),
    DEFAULT_COLLECTOR_SCHEDULE_START_TIME,
    DEFAULT_COLLECTOR_SCHEDULE_END_TIME,
)


def schedule_from_payload(value: object) -> CollectorSchedule:
    """Lê uma agenda recebida pela rede, onde nada pode ser presumido."""
    if not isinstance(value, dict):
        raise ValueError("Agenda do coletor inválida.")
    raw_weekdays = value.get("weekdays")
    if not isinstance(raw_weekdays, list) or any(
        isinstance(day, bool) or not isinstance(day, int) for day in raw_weekdays
    ):
        raise ValueError("Dias da agenda do coletor inválidos.")
    weekdays = frozenset(raw_weekdays)
    if not weekdays or any(day < 0 or day > 6 for day in weekdays):
        raise ValueError("Dias da agenda do coletor inválidos.")
    try:
        start_time = time.fromisoformat(str(value["start_time"]))
        end_time = time.fromisoformat(str(value["end_time"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Horários da agenda do coletor inválidos.") from exc
    if start_time >= end_time:
        raise ValueError("Faixa de horário da agenda do coletor inválida.")
    return CollectorSchedule(weekdays, start_time, end_time)


def schedule_from_settings(settings: AppSetting) -> CollectorSchedule:
    return CollectorSchedule(
        _weekdays_from_field(settings.collector_schedule_weekdays),
        settings.collector_schedule_start_time,
        settings.collector_schedule_end_time,
    )


def valid_poll_interval(value: object) -> int:
    """Valida um intervalo entre leituras vindo de fora do processo."""
    if isinstance(value, bool):
        raise ValueError("Intervalo entre leituras inválido.")
    try:
        interval = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("Intervalo entre leituras inválido.") from exc
    if not MIN_POLL_INTERVAL_SECONDS <= interval <= MAX_POLL_INTERVAL_SECONDS:
        raise ValueError("Intervalo entre leituras fora da faixa permitida.")
    return interval


def valid_agent_check_interval(value: object) -> int:
    """Valida um intervalo de verificação vindo de fora do processo."""
    if isinstance(value, bool):
        raise ValueError("Intervalo de verificação inválido.")
    try:
        interval = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("Intervalo de verificação inválido.") from exc
    if not MIN_AGENT_CHECK_INTERVAL_SECONDS <= interval <= MAX_AGENT_CHECK_INTERVAL_SECONDS:
        raise ValueError("Intervalo de verificação fora da faixa permitida.")
    return interval


def _weekdays_from_field(weekdays: str) -> frozenset[int]:
    """Campo `0,1,2,3,4` da tabela; um valor corrompido não abre a janela."""
    try:
        return frozenset(int(value) for value in weekdays.split(","))
    except ValueError:
        return frozenset()


def collector_schedule_is_active(
    weekdays: str,
    start_time: time,
    end_time: time,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a collector may run in the configured B3-time window."""
    return CollectorSchedule(
        _weekdays_from_field(weekdays), start_time, end_time
    ).is_active(now)


def default_collector_settings() -> AppSetting:
    return AppSetting(
        id=1,
        theme=DEFAULT_THEME,
        collector_mode=CollectorMode.EXCEL,
        collector_destination=CollectorDestination.REMOTE,
        poll_interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
        agent_check_interval_seconds=DEFAULT_AGENT_CHECK_INTERVAL_SECONDS,
        collector_schedule_weekdays=DEFAULT_COLLECTOR_SCHEDULE_WEEKDAYS,
        collector_schedule_start_time=DEFAULT_COLLECTOR_SCHEDULE_START_TIME,
        collector_schedule_end_time=DEFAULT_COLLECTOR_SCHEDULE_END_TIME,
    )
