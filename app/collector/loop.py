"""Loop único de coleta RTD, independente de para onde a cotação vai.

O que muda entre coletar para o VPS e coletar para o banco desta máquina é
apenas *de onde vem a configuração* e *para onde vai a leitura*. O resto --
os dois relógios, a espera pelo ProfitChart, a troca de provedor quando o
modo muda, o silêncio fora da agenda -- é idêntico, e mora aqui.
"""

from __future__ import annotations

import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from logging import Logger
from typing import Protocol

from app.collector.profit_detector import ProfitDetector
from app.collector.providers import CollectorProviderManager
from app.collector.rtd import Instrument, QuoteValue
from app.collector.settings import CollectorSchedule
from app.core.domain import MARKET_TIMEZONE
from app.models import CollectorMode


@dataclass(frozen=True, slots=True)
class CollectorConfiguration:
    """Tudo que um ciclo precisa saber, já validado pela origem."""

    collector_mode: CollectorMode
    poll_interval_seconds: int
    agent_check_interval_seconds: int
    schedule: CollectorSchedule
    instruments: tuple[Instrument, ...]
    option_keys: dict[int, tuple[int, int]] = field(default_factory=dict)
    refresh_requested: bool = False
    paused: bool = False

    @property
    def collection_fingerprint(self) -> tuple[object, ...]:
        """O que, ao mudar, pede uma leitura nova em vez de só uma checagem.

        O intervalo de verificação e o pedido manual ficam de fora de
        propósito: o primeiro não altera o que seria lido, e o segundo já
        dispara a leitura por conta própria.
        """
        return (
            self.collector_mode,
            self.poll_interval_seconds,
            self.schedule,
            self.instruments,
            tuple(sorted(self.option_keys.items())),
            # Religar entra aqui para a coleta voltar na hora, e não no fim
            # do intervalo de leitura -- que pode ser de cinco minutos.
            self.paused,
        )


class ConfigurationSource(Protocol):
    def configuration(self) -> CollectorConfiguration: ...


class QuoteSink(Protocol):
    @property
    def destination_label(self) -> str: ...

    def publish(
        self, values: list[QuoteValue], option_keys: dict[int, tuple[int, int]]
    ) -> None: ...

    def report_failure(self, error: Exception) -> None: ...


@dataclass(slots=True)
class CollectorDeadlines:
    """Relógios independentes do coletor.

    A consulta de configuração é barata e não toca o ProfitChart. A leitura
    RTD, por outro lado, só é disparada no seu próprio prazo (ou por pedido
    manual já recebido na configuração). Manter os dois prazos explícitos
    evita que o menor intervalo transforme o outro em polling frequente.
    """

    next_configuration_at: float = 0.0
    next_quote_at: float = float("inf")

    def configuration_due(self, now: float) -> bool:
        return now >= self.next_configuration_at

    def quote_due(self, now: float) -> bool:
        return now >= self.next_quote_at

    def schedule_configuration(self, now: float, interval: int) -> None:
        self.next_configuration_at = now + interval

    def schedule_quote(self, now: float, interval: int) -> None:
        self.next_quote_at = now + interval

    def request_quote_now(self) -> None:
        self.next_quote_at = 0.0

    def sleep_seconds(self, now: float) -> float:
        return max(0.0, min(self.next_configuration_at, self.next_quote_at) - now)


def run_collector_loop(
    *,
    source: ConfigurationSource,
    sink: QuoteSink,
    providers: CollectorProviderManager,
    detector: ProfitDetector,
    logger: Logger,
    initial_schedule: CollectorSchedule,
    initial_check_interval: int,
    on_configuration: Callable[[CollectorConfiguration], None] = lambda _: None,
    should_continue: Callable[[], bool] = lambda: True,
    monotonic: Callable[[], float] = time_module.monotonic,
    sleep: Callable[[float], None] = time_module.sleep,
    market_now: Callable[[], datetime] = lambda: datetime.now(MARKET_TIMEZONE),
) -> None:
    """Coleta até ser interrompido, entregando ao destino que `sink` decide.

    `initial_schedule` e `initial_check_interval` cobrem o intervalo entre o
    início do processo e a primeira configuração bem-sucedida: sem eles, uma
    origem indisponível deixaria o loop sem noção de agenda nenhuma.

    `should_continue` é consultado antes de cada iteração. É por ele que o
    coletor local morre junto do supervisor que o iniciou, em vez de virar um
    órfão segurando COM depois que o processo pai já saiu.
    """

    deadlines = CollectorDeadlines()
    schedule = initial_schedule
    check_interval = initial_check_interval
    configuration: CollectorConfiguration | None = None
    idle_reason: str | None = None
    try:
        while should_continue():
            now = monotonic()
            if deadlines.configuration_due(now):
                previous = configuration
                schedule_was_active = schedule.is_active(market_now())
                try:
                    configuration = source.configuration()
                    collection_changed = previous is None or (
                        configuration.collection_fingerprint != previous.collection_fingerprint
                    )
                    check_interval = configuration.agent_check_interval_seconds
                    schedule = configuration.schedule
                    deadlines.schedule_configuration(now, check_interval)
                    try:
                        on_configuration(configuration)
                    except OSError as exc:
                        logger.warning(
                            "Não foi possível atualizar o estado local do agente: %s", exc
                        )
                    schedule_just_opened = not schedule_was_active and schedule.is_active(
                        market_now()
                    )
                    if (
                        collection_changed
                        or configuration.refresh_requested
                        or schedule_just_opened
                    ):
                        deadlines.request_quote_now()
                except Exception as exc:
                    configuration = None
                    logger.warning("Não foi possível consultar a configuração do coletor: %s", exc)
                    sink.report_failure(exc)
                    deadlines.schedule_configuration(now, check_interval)

            now = monotonic()
            if configuration is not None and deadlines.quote_due(now):
                poll_interval = configuration.poll_interval_seconds
                if configuration.paused:
                    # Pausa não é parada: o processo continua vivo e voltando
                    # a perguntar, para retomar sozinho quando a tela religar.
                    providers.close()
                    if idle_reason != "paused":
                        logger.info("Coleta pausada nas Configurações.")
                        idle_reason = "paused"
                    deadlines.schedule_quote(now, poll_interval)
                elif not schedule.is_active(market_now()):
                    providers.close()
                    if idle_reason != "schedule":
                        logger.info("Agente aguardando a próxima janela da agenda de coleta.")
                        idle_reason = "schedule"
                    deadlines.schedule_quote(now, poll_interval)
                else:
                    try:
                        if not detector.is_running():
                            providers.close()
                            if idle_reason != "profit-closed":
                                logger.info("Agente aguardando o ProfitChart ser aberto.")
                                idle_reason = "profit-closed"
                        else:
                            instruments = list(configuration.instruments)
                            values = (
                                providers.get(configuration.collector_mode).fetch(instruments)
                                if instruments
                                else []
                            )
                            sink.publish(values, configuration.option_keys)
                            logger.info(
                                "Ciclo de cotações entregue %s (%s instrumentos).",
                                sink.destination_label,
                                len(values),
                            )
                            idle_reason = None
                    except Exception as exc:
                        providers.close()
                        logger.warning("Leitura de cotações falhou: %s", exc)
                        sink.report_failure(exc)
                    finally:
                        # Inclusive quando o Profit está fechado ou falha: a
                        # próxima tentativa é no intervalo de leitura, não no
                        # intervalo de configuração.
                        deadlines.schedule_quote(monotonic(), poll_interval)

            sleep(deadlines.sleep_seconds(monotonic()))
    finally:
        providers.close()
