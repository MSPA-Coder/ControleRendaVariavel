"""Agente Windows que coleta RTD e entrega cotações ao VPS por HTTPS.

Este módulo não cria a aplicação Flask e não acessa PostgreSQL. Isso mantém o
ProfitChart/COM no Windows e deixa o VPS apenas receber leituras autenticadas.
O laço em si mora em ``app.collector_loop``; aqui ficam só a origem e o
destino HTTP -- o que fazer com a rede, e nada sobre quando coletar.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.collector import CollectorProviderManager, ManagedQuoteProvider
from app.collector_loop import CollectorConfiguration, run_collector_loop
from app.collector_settings import (
    DEFAULT_AGENT_CHECK_INTERVAL_SECONDS,
    DEFAULT_COLLECTOR_SCHEDULE,
    CollectorSchedule,
    schedule_from_payload,
    valid_agent_check_interval,
    valid_poll_interval,
)
from app.models import CollectorMode
from app.profit_detector import ProfitDetector, WindowsProfitDetector
from app.rtd import ExcelRtdQuoteProvider, Instrument, QuoteValue
from app.rtd_direct import DirectRtdQuoteProvider

CONFIG_PATH = Path(".docker-local") / "remote-collector.env"
AGENT_LOGGER_NAME = "controle_renda_variavel.remote_collector"


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


def _load_agent_check_interval(path: Path) -> int:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("Estado inválido.")
        return valid_agent_check_interval(state.get("agent_check_interval_seconds"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return DEFAULT_AGENT_CHECK_INTERVAL_SECONDS


def _load_collector_schedule(path: Path) -> CollectorSchedule:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("Estado inválido.")
        return schedule_from_payload(state.get("collector_schedule"))
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


def _instrument_sets(
    configuration: dict[str, Any],
) -> tuple[list[Instrument], dict[int, tuple[int, int]]]:
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


def _quotes_payload(
    values: list[QuoteValue], option_keys: dict[int, tuple[int, int]]
) -> dict[str, object]:
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


@dataclass(frozen=True, slots=True)
class HttpConfigurationSource:
    """Configuração vinda do VPS: tudo aqui é entrada não confiável."""

    api: CollectorApi

    def configuration(self) -> CollectorConfiguration:
        payload = self.api.configuration()
        instruments, option_keys = _instrument_sets(payload)
        return CollectorConfiguration(
            collector_mode=CollectorMode(str(payload["collector_mode"])),
            poll_interval_seconds=valid_poll_interval(payload.get("poll_interval_seconds")),
            agent_check_interval_seconds=valid_agent_check_interval(
                payload.get("agent_check_interval_seconds")
            ),
            schedule=schedule_from_payload(payload.get("collector_schedule")),
            instruments=tuple(instruments),
            option_keys=option_keys,
            refresh_requested=payload.get("refresh_requested") is True,
            paused=payload.get("paused") is True,
        )


@dataclass(frozen=True, slots=True)
class HttpQuoteSink:
    """Entrega ao VPS. Nunca toca banco: só sabe falar HTTPS com um token."""

    api: CollectorApi

    @property
    def destination_label(self) -> str:
        return "ao VPS"

    def publish(
        self, values: list[QuoteValue], option_keys: dict[int, tuple[int, int]]
    ) -> None:
        self.api.send_quotes(_quotes_payload(values, option_keys))

    def report_failure(self, error: Exception) -> None:
        self.api.send_failure(error)


def remote_loop_arguments(project_dir: Path) -> dict[str, object]:
    """Origem e destino para entregar ao VPS por HTTPS.

    Levanta ``RuntimeError`` quando a URL ou o token não estão configurados:
    escolher este destino sem ter com quem falar é um erro de instalação, e
    falhar aqui é melhor do que coletar para lugar nenhum.

    A agenda e o intervalo iniciais vêm do arquivo de estado local, e não de
    um padrão fixo, para o agente não sair coletando fora de hora enquanto a
    primeira consulta ao VPS ainda não voltou.
    """
    config = _environment(project_dir)
    api = CollectorApi(config.get("COLLECTOR_REMOTE_URL", ""), _read_token(project_dir, config))
    state_path = _state_path(project_dir)
    return {
        "source": HttpConfigurationSource(api),
        "sink": HttpQuoteSink(api),
        "initial_schedule": _load_collector_schedule(state_path),
        "initial_check_interval": _load_agent_check_interval(state_path),
        "on_configuration": lambda configuration: _store_agent_state(
            state_path,
            configuration.agent_check_interval_seconds,
            configuration.schedule,
        ),
    }


def run(project_dir: Path, *, profit_detector: ProfitDetector | None = None) -> None:
    """Entrada dedicada ao modo remoto, sem criar a aplicação Flask.

    A tarefa unificada do Windows usa ``poll-rtd``, que decide o destino pela
    configuração. Este caminho continua para a máquina que só entrega ao VPS
    e prefere não ter credencial de banco alguma no processo.
    """
    run_collector_loop(
        providers=CollectorProviderManager(_provider_factory(_environment(project_dir))),
        detector=profit_detector or WindowsProfitDetector(),
        logger=_logger(project_dir),
        **remote_loop_arguments(project_dir),  # type: ignore[arg-type]
    )


def main() -> None:
    if os.name != "nt":
        raise SystemExit("O agente remoto de cotações deve ser executado no Windows.")
    run(Path(__file__).resolve().parent.parent)


if __name__ == "__main__":
    main()
