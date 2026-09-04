"""O lado banco da coleta: o que ler antes de um ciclo e como gravá-lo.

Duas entradas chegam aqui pelo mesmo caminho de escrita: o processo local,
que lê o RTD e grava direto, e o endpoint que recebe as cotações do agente
Windows por HTTPS. Ter uma implementação só é o que garante que os dois
destinos gravem exatamente os mesmos campos -- inclusive o snapshot diário
de ``quote_history``, fácil de esquecer em uma segunda cópia.

Nada aqui faz ``commit``: quem inicia a escrita é dono do limite
transacional, como no resto do projeto.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import joinedload

from app import db
from app.collector_loop import CollectorConfiguration
from app.collector_settings import default_collector_settings, schedule_from_settings
from app.domain import MARKET_TIMEZONE
from app.models import (
    AppSetting,
    OptionContract,
    OptionPosition,
    OptionQuote,
    Position,
    Quote,
)
from app.routes.helpers import upsert_quote_history
from app.rtd import Instrument, QuoteValue


def collector_settings_row() -> AppSetting:
    """A linha única de configuração, criada na primeira coleta se faltar."""
    settings = db.session.get(AppSetting, 1)
    if settings is None:
        settings = default_collector_settings()
        db.session.add(settings)
        db.session.commit()
    return settings


def load_collector_positions() -> tuple[list[Position], list[OptionPosition]]:
    """Posições abertas com os tickers já carregados, na ordem estável do id."""
    positions = list(
        db.session.scalars(
            select(Position).options(joinedload(Position.ticker_ref)).order_by(Position.id)
        )
    )
    option_positions = list(
        db.session.scalars(
            select(OptionPosition)
            .options(
                joinedload(OptionPosition.contract).joinedload(OptionContract.ticker_ref),
                joinedload(OptionPosition.contract).joinedload(
                    OptionContract.underlying_ticker_ref
                ),
            )
            .order_by(OptionPosition.id)
        ).unique()
    )
    return positions, option_positions


def option_instrument_keys(option_position_id: int) -> tuple[int, int]:
    """Chaves sintéticas da opção e do seu ativo-objeto.

    Ambas são negativas para nunca colidirem com um ``position_id`` real, que
    é a chave usada nas leituras de ações.
    """
    option_key = -option_position_id * 2
    return option_key, option_key - 1


def instruments_for(
    positions: Sequence[Position], option_positions: Sequence[OptionPosition]
) -> tuple[list[Instrument], dict[int, tuple[int, int]]]:
    instruments = [
        Instrument(item.id, item.ticker, item.rtd_market_code, item.side.value)
        for item in positions
    ]
    option_keys: dict[int, tuple[int, int]] = {}
    for item in option_positions:
        option_key, underlying_key = option_instrument_keys(item.id)
        option_keys[item.id] = (option_key, underlying_key)
        instruments.extend(
            [
                Instrument(
                    option_key,
                    item.contract.ticker_ref.symbol,
                    item.contract.ticker_ref.rtd_market_code,
                ),
                Instrument(
                    underlying_key,
                    item.contract.underlying_ticker_ref.symbol,
                    item.contract.underlying_ticker_ref.rtd_market_code,
                ),
            ]
        )
    return instruments, option_keys


@dataclass(frozen=True, slots=True)
class OptionReading:
    """Uma opção e o preço do seu ativo-objeto no mesmo instante.

    O ativo-objeto entra aqui como dois números, e não como um `QuoteValue`
    completo, porque é só isso que a linha de `option_quotes` guarda dele.
    Um `QuoteValue` inteiro obrigaria quem monta a leitura a inventar um
    `previous_close` e um status que ninguém lê.
    """

    option_position_id: int
    option: QuoteValue
    underlying_last_price: Decimal
    underlying_history_price: Decimal


def split_readings(
    values: Sequence[QuoteValue], option_keys: Mapping[int, tuple[int, int]]
) -> tuple[list[QuoteValue], list[OptionReading]]:
    """Separa um ciclo do coletor nas duas formas que a escrita espera."""
    by_key = {value.position_id: value for value in values}
    stock_values = [value for value in values if value.position_id > 0]
    option_readings = [
        OptionReading(
            option_position_id=option_position_id,
            option=by_key[option_key],
            underlying_last_price=by_key[underlying_key].last_price,
            underlying_history_price=by_key[underlying_key].quote_history_price,
        )
        for option_position_id, (option_key, underlying_key) in sorted(option_keys.items())
    ]
    return stock_values, option_readings


def persist_readings(
    stock_values: Sequence[QuoteValue], option_readings: Sequence[OptionReading]
) -> None:
    """Grava um ciclo de leitura, venha ele do processo local ou do agente.

    Levanta ``ValueError`` quando uma leitura aponta para uma posição que não
    existe mais -- o caso de uma posição encerrada entre a configuração e a
    entrega, que do lado do agente remoto chega como entrada não confiável.
    """
    position_ids = {value.position_id for value in stock_values}
    positions = {
        item.id: item
        for item in db.session.scalars(select(Position).where(Position.id.in_(position_ids)))
    }
    option_position_ids = {reading.option_position_id for reading in option_readings}
    option_positions = {
        item.id: item
        for item in db.session.scalars(
            select(OptionPosition)
            .options(joinedload(OptionPosition.contract))
            .where(OptionPosition.id.in_(option_position_ids))
        ).unique()
    }
    if len(positions) != len(position_ids) or len(option_positions) != len(option_position_ids):
        raise ValueError("posição inexistente")

    ticker_prices: dict[int, tuple[Decimal, datetime]] = {}
    for value in stock_values:
        statement = insert(Quote).values(
            position_id=value.position_id,
            last_price=value.last_price,
            previous_close=value.previous_close,
            instrument_status=value.instrument_status,
            source_status="online",
            error_message=None,
            observed_at=value.observed_at,
        )
        db.session.execute(
            statement.on_conflict_do_update(
                index_elements=[Quote.position_id],
                set_={
                    "last_price": statement.excluded.last_price,
                    "previous_close": statement.excluded.previous_close,
                    "instrument_status": statement.excluded.instrument_status,
                    "source_status": "online",
                    "error_message": None,
                    "observed_at": statement.excluded.observed_at,
                },
            )
        )
        ticker_prices[positions[value.position_id].ticker_id] = (
            value.quote_history_price,
            value.observed_at,
        )

    for reading in option_readings:
        option_value = reading.option
        statement = insert(OptionQuote).values(
            option_position_id=reading.option_position_id,
            last_price=option_value.last_price,
            previous_close=option_value.previous_close,
            underlying_price=reading.underlying_last_price,
            instrument_status=option_value.instrument_status,
            source_status="online",
            error_message=None,
            observed_at=option_value.observed_at,
        )
        db.session.execute(
            statement.on_conflict_do_update(
                index_elements=[OptionQuote.option_position_id],
                set_={
                    "last_price": statement.excluded.last_price,
                    "previous_close": statement.excluded.previous_close,
                    "underlying_price": statement.excluded.underlying_price,
                    "instrument_status": statement.excluded.instrument_status,
                    "source_status": "online",
                    "error_message": None,
                    "observed_at": statement.excluded.observed_at,
                },
            )
        )
        contract = option_positions[reading.option_position_id].contract
        ticker_prices[contract.ticker_id] = (
            option_value.quote_history_price,
            option_value.observed_at,
        )
        ticker_prices[contract.underlying_ticker_id] = (
            reading.underlying_history_price,
            option_value.observed_at,
        )

    # Um snapshot por ticker por dia, não a cada poll -- ver a docstring de
    # QuoteHistory para o porquê.
    upsert_quote_history(
        (
            ticker_id,
            price,
            observed_at.astimezone(MARKET_TIMEZONE).date(),
            observed_at,
        )
        for ticker_id, (price, observed_at) in ticker_prices.items()
    )


def record_agent_online(settings: AppSetting) -> None:
    """Pulso de vida do coletor, igual para os dois destinos."""
    settings.collector_agent_seen_at = datetime.now(UTC)
    settings.collector_agent_status = "online"
    settings.collector_agent_error = None
    settings.collector_refresh_requested_at = None


def record_agent_failure(settings: AppSetting, error: str) -> None:
    settings.collector_agent_seen_at = datetime.now(UTC)
    settings.collector_agent_status = "error"
    settings.collector_agent_error = error[:250]


class DatabaseConfigurationSource:
    """Configuração lida da própria tabela, sem passar pela rede."""

    def configuration(self) -> CollectorConfiguration:
        # A sessão do processo é longa; sem expirar, ele leria para sempre o
        # snapshot da primeira consulta e nunca veria uma posição nova.
        db.session.expire_all()
        settings = collector_settings_row()
        positions, option_positions = load_collector_positions()
        instruments, option_keys = instruments_for(positions, option_positions)
        configuration = CollectorConfiguration(
            collector_mode=settings.collector_mode,
            poll_interval_seconds=settings.poll_interval_seconds,
            agent_check_interval_seconds=settings.agent_check_interval_seconds,
            schedule=schedule_from_settings(settings),
            instruments=tuple(instruments),
            option_keys=option_keys,
            refresh_requested=settings.collector_refresh_requested_at is not None,
        )
        # Nada foi escrito: encerrar a transação de leitura evita segurar um
        # snapshot do PostgreSQL entre um ciclo e o próximo.
        db.session.rollback()
        return configuration


class DatabaseQuoteSink:
    """Grava direto no PostgreSQL desta máquina."""

    @property
    def destination_label(self) -> str:
        return "ao banco local"

    def publish(
        self, values: list[QuoteValue], option_keys: dict[int, tuple[int, int]]
    ) -> None:
        persist_readings(*split_readings(values, option_keys))
        record_agent_online(collector_settings_row())
        db.session.commit()

    def report_failure(self, error: Exception) -> None:
        message = str(error)[:250]
        db.session.rollback()
        db.session.query(Quote).update({"source_status": "error", "error_message": message})
        db.session.query(OptionQuote).update({"source_status": "error", "error_message": message})
        record_agent_failure(collector_settings_row(), message)
        db.session.commit()
