"""Agente Windows que coleta RTD e entrega cotações ao VPS por HTTPS.

Este módulo não cria a aplicação Flask e não acessa PostgreSQL. Isso mantém o
ProfitChart/COM no Windows e deixa o VPS apenas receber leituras autenticadas.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import time as datetime_time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.collector import CollectorProviderManager, ManagedQuoteProvider
from app.domain import MARKET_TIMEZONE
from app.models import CollectorMode
from app.rtd import ExcelRtdQuoteProvider, Instrument, QuoteValue
from app.rtd_direct import DirectRtdQuoteProvider
from app.rtd_service import ProfitDetector, WindowsProfitDetector

CONFIG_PATH = Path(".docker-local") / "remote-collector.env"
AGENT_LOGGER_NAME = "controle_renda_variavel.remote_collector"
DEFAULT_AGENT_CHECK_INTERVAL_SECONDS = 30
MIN_AGENT_CHECK_INTERVAL_SECONDS = 5
MAX_AGENT_CHECK_INTERVAL_SECONDS = 3600
DEFAULT_COLLECTOR_SCHEDULE_WEEKDAYS = frozenset({0, 1, 2, 3, 4})
DEFAULT_COLLECTOR_SCHEDULE_START_TIME = datetime_time(9, 45)
DEFAULT_COLLECTOR_SCHEDULE_END_TIME = datetime_time(18, 10)


@dataclass(frozen=True, slots=True)
class CollectorSchedule:
    weekdays: frozenset[int]
    start_time: datetime_time
    end_time: datetime_time

    def is_active(self, now: datetime) -> bool:
        local_time = now.astimezone(MARKET_TIMEZONE)
        return (
            local_time.weekday() in self.weekdays
            and self.start_time <= local_time.timetz().replace(tzinfo=None) < self.end_time
        )

    def as_payload(self) -> dict[str, object]:
        return {
            "weekdays": sorted(self.weekdays),
            "start_time": self.start_time.strftime("%H:%M"),
            "end_time": self.end_time.strftime("%H:%M"),
        }


DEFAULT_COLLECTOR_SCHEDULE = CollectorSchedule(
    DEFAULT_COLLECTOR_SCHEDULE_WEEKDAYS,
    DEFAULT_COLLECTOR_SCHEDULE_START_TIME,
    DEFAULT_COLLECTOR_SCHEDULE_END_TIME,
)


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("\"'")
    return values


def _environment(project_dir: Path) -> dict[str, str]:
    values = _read_dotenv(project_dir / ".env")
    values.update(_read_dotenv(project_dir / CONFIG_PATH))
    return values


def _read_token(project_dir: Path, config: dict[str, str]) -> str:
    raw_path = config.get("COLLECTOR_AGENT_TOKEN_FILE", ".secrets/collector_agent_token")
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_dir / path
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Token do agente não disponível em {path}.") from exc
    if len(token) < 32:
        raise RuntimeError("Token do agente inválido.")
    return token


def _local_data_directory(project_dir: Path) -> Path:
    """Diretório fixo do agente, sem aceitar caminhos por variável de ambiente."""
    if os.name == "nt":
        return Path.home() / "AppData" / "Local"
    return project_dir / ".docker-local"


def _logger(project_dir: Path) -> logging.Logger:
    directory = _local_data_directory(project_dir)
    log_path = directory / "ControleRendaVariavel" / "remote-collector.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(AGENT_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(log_path, maxBytes=1_048_576, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def _state_path(project_dir: Path) -> Path:
    directory = _local_data_directory(project_dir)
    return directory / "ControleRendaVariavel" / "remote-collector-state.json"


def _valid_agent_check_interval(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Intervalo de verificação inválido.")
    try:
        interval = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Intervalo de verificação inválido.") from exc
    if not MIN_AGENT_CHECK_INTERVAL_SECONDS <= interval <= MAX_AGENT_CHECK_INTERVAL_SECONDS:
        raise ValueError("Intervalo de verificação fora da faixa permitida.")
    return interval


def _schedule_from_payload(value: object) -> CollectorSchedule:
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
        start_time = datetime_time.fromisoformat(str(value["start_time"]))
        end_time = datetime_time.fromisoformat(str(value["end_time"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Horários da agenda do coletor inválidos.") from exc
    if start_time >= end_time:
        raise ValueError("Faixa de horário da agenda do coletor inválida.")
    return CollectorSchedule(weekdays, start_time, end_time)


def _load_agent_check_interval(path: Path) -> int:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("Estado inválido.")
        return _valid_agent_check_interval(state.get("agent_check_interval_seconds"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return DEFAULT_AGENT_CHECK_INTERVAL_SECONDS


def _load_collector_schedule(path: Path) -> CollectorSchedule:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("Estado inválido.")
        return _schedule_from_payload(state.get("collector_schedule"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return DEFAULT_COLLECTOR_SCHEDULE


def _store_agent_state(path: Path, interval: int, schedule: CollectorSchedule) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "agent_check_interval_seconds": interval,
                "collector_schedule": schedule.as_payload(),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _store_agent_check_interval(path: Path, interval: int) -> None:
    """Compatibilidade com o arquivo de estado anterior, sem perder a agenda."""
    _store_agent_state(path, interval, _load_collector_schedule(path))


class CollectorApi:
    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 15) -> None:
        if not base_url.startswith("https://"):
            raise RuntimeError("COLLECTOR_REMOTE_URL deve usar HTTPS.")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(self, path: str, *, payload: dict[str, object] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
            method="POST" if data is not None else "GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                decoded = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("Comunicação com o VPS falhou.") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("Resposta inválida do VPS.")
        return decoded

    def configuration(self) -> dict[str, Any]:
        return self._request("/api/collector/configuration")

    def send_quotes(self, payload: dict[str, object]) -> None:
        self._request("/api/collector/quotes", payload=payload)

    def send_failure(self, error: Exception) -> None:
        try:
            self._request("/api/collector/failure", payload={"error": str(error)[:250]})
        except RuntimeError:
            return


def _provider_factory(config: dict[str, str]):
    def factory(mode: CollectorMode) -> ManagedQuoteProvider:
        common = {
            "prog_id": config.get("RTD_PROG_ID", "rtdtrading.rtdserver"),
            "timeout_seconds": float(config.get("RTD_TIMEOUT_SECONDS", "10")),
        }
        if mode == CollectorMode.DIRECT:
            return DirectRtdQuoteProvider(
                **common,
                refresh_seconds=min(float(config.get("RTD_REFRESH_SECONDS", "2")), 0.25),
            )
        return ExcelRtdQuoteProvider(
            **common,
            refresh_seconds=float(config.get("RTD_REFRESH_SECONDS", "2")),
            visible=config.get("RTD_EXCEL_VISIBLE", "false").lower() == "true",
        )

    return factory


def _instrument_sets(configuration: dict[str, Any]) -> tuple[list[Instrument], dict[int, tuple[int, int]]]:
    instruments: list[Instrument] = []
    option_keys: dict[int, tuple[int, int]] = {}
    positions = configuration.get("positions", [])
    option_positions = configuration.get("option_positions", [])
    if not isinstance(positions, list) or not isinstance(option_positions, list):
        raise RuntimeError("Configuração de instrumentos inválida.")
    for item in positions:
        if not isinstance(item, dict):
            raise RuntimeError("Configuração de posição inválida.")
        instruments.append(
            Instrument(
                int(item["position_id"]),
                str(item["ticker"]),
                str(item["market_code"]),
                str(item["side"]),
            )
        )
    for item in option_positions:
        if not isinstance(item, dict):
            raise RuntimeError("Configuração de opção inválida.")
        option_position_id = int(item["option_position_id"])
        option_key = -option_position_id * 2
        underlying_key = option_key - 1
        option_keys[option_position_id] = (option_key, underlying_key)
        instruments.extend(
            [
                Instrument(option_key, str(item["ticker"]), str(item["market_code"])),
                Instrument(
                    underlying_key,
                    str(item["underlying_ticker"]),
                    str(item["underlying_market_code"]),
                ),
            ]
        )
    return instruments, option_keys


def _serialized(value: QuoteValue) -> dict[str, str | int]:
    return {
        "last_price": str(value.last_price),
        "previous_close": str(value.previous_close),
        "instrument_status": value.instrument_status,
        "observed_at": value.observed_at.astimezone(UTC).isoformat(),
        "history_price": str(value.quote_history_price),
    }


def _quotes_payload(values: list[QuoteValue], option_keys: dict[int, tuple[int, int]]) -> dict[str, object]:
    by_id = {value.position_id: value for value in values}
    positions = [
        {"position_id": value.position_id, **_serialized(value)}
        for value in values
        if value.position_id > 0
    ]
    option_positions: list[dict[str, str | int]] = []
    for option_position_id, (option_key, underlying_key) in option_keys.items():
        option_value = by_id[option_key]
        underlying_value = by_id[underlying_key]
        option_positions.append(
            {
                "option_position_id": option_position_id,
                **_serialized(option_value),
                "underlying_price": str(underlying_value.last_price),
                "underlying_history_price": str(underlying_value.quote_history_price),
            }
        )
    return {"positions": positions, "option_positions": option_positions}


def run(project_dir: Path, *, profit_detector: ProfitDetector | None = None) -> None:
    config = _environment(project_dir)
    api = CollectorApi(config.get("COLLECTOR_REMOTE_URL", ""), _read_token(project_dir, config))
    logger = _logger(project_dir)
    providers = CollectorProviderManager(_provider_factory(config))
    state_path = _state_path(project_dir)
    agent_check_interval = _load_agent_check_interval(state_path)
    collector_schedule = _load_collector_schedule(state_path)
    detector = profit_detector or WindowsProfitDetector()
    next_poll_at = 0.0
    idle_reason: str | None = None
    try:
        while True:
            if not collector_schedule.is_active(datetime.now(MARKET_TIMEZONE)):
                providers.close()
                if idle_reason != "schedule":
                    logger.info("Agente aguardando a próxima janela da agenda de coleta.")
                    idle_reason = "schedule"
                time.sleep(agent_check_interval)
                continue
            try:
                profit_running = detector.is_running()
            except RuntimeError as exc:
                providers.close()
                if idle_reason != "profit-check-error":
                    logger.warning("Agente aguardando verificação local do ProfitChart: %s", exc)
                    idle_reason = "profit-check-error"
                time.sleep(agent_check_interval)
                continue
            if not profit_running:
                providers.close()
                if idle_reason != "profit-closed":
                    logger.info("Agente aguardando o ProfitChart ser aberto.")
                    idle_reason = "profit-closed"
                time.sleep(agent_check_interval)
                continue
            try:
                configuration = api.configuration()
                agent_check_interval = _valid_agent_check_interval(
                    configuration.get("agent_check_interval_seconds")
                )
                collector_schedule = _schedule_from_payload(configuration.get("collector_schedule"))
                try:
                    _store_agent_state(state_path, agent_check_interval, collector_schedule)
                except OSError as exc:
                    logger.warning("Não foi possível atualizar o estado local do agente: %s", exc)
                if not collector_schedule.is_active(datetime.now(MARKET_TIMEZONE)):
                    providers.close()
                    idle_reason = "schedule"
                    time.sleep(agent_check_interval)
                    continue
                now = time.monotonic()
                refresh_requested = configuration.get("refresh_requested") is True
                if now >= next_poll_at or refresh_requested:
                    mode = CollectorMode(str(configuration["collector_mode"]))
                    interval = int(configuration["poll_interval_seconds"])
                    instruments, option_keys = _instrument_sets(configuration)
                    values = providers.get(mode).fetch(instruments) if instruments else []
                    api.send_quotes(_quotes_payload(values, option_keys))
                    next_poll_at = time.monotonic() + interval
                    logger.info("Ciclo de cotações entregue ao VPS (%s instrumentos).", len(values))
                idle_reason = None
            except Exception as exc:
                logger.warning("Ciclo do coletor falhou: %s", exc)
                api.send_failure(exc)
                next_poll_at = time.monotonic() + agent_check_interval
            time.sleep(agent_check_interval)
    finally:
        providers.close()


def main() -> None:
    if os.name != "nt":
        raise SystemExit("O agente remoto de cotações deve ser executado no Windows.")
    run(Path(__file__).resolve().parent.parent)


if __name__ == "__main__":
    main()
