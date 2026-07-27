from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.models import AppSetting, CollectorMode

MIN_POLL_INTERVAL_SECONDS = 1
MAX_POLL_INTERVAL_SECONDS = 3600
DEFAULT_POLL_INTERVAL_SECONDS = 2


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


def default_collector_settings() -> AppSetting:
    return AppSetting(
        id=1,
        collector_mode=CollectorMode.EXCEL,
        poll_interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
    )
