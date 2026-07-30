from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal
from threading import Lock
from typing import cast

from flask import current_app, request
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app import db
from app.collector_settings import DEFAULT_POLL_INTERVAL_SECONDS
from app.models import (
    AppSetting,
    Broker,
    OptionContract,
    OptionExpiration,
    Position,
    PositionKind,
    Ticker,
)
from app.portfolio import PositionView
from app.rtd_service import RtdService


def positions_query(
    position_kind: PositionKind | None = None, broker: str | None = None
) -> list[Position]:
    statement = (
        select(Position)
        .join(Position.broker_ref)
        .join(Position.ticker_ref)
        .options(
            joinedload(Position.quote),
            joinedload(Position.broker_ref),
            joinedload(Position.ticker_ref),
        )
        .order_by(Ticker.currency, Broker.name, Ticker.symbol, Position.opened_on)
    )
    if position_kind is not None:
        statement = statement.where(Position.position_kind == position_kind)
    if broker:
        statement = statement.where(Broker.name == broker)
    return list(db.session.scalars(statement).unique())


def brokers() -> list[str]:
    statement = select(Broker.name).order_by(Broker.name)
    return list(db.session.scalars(statement))


def broker_records() -> list[Broker]:
    return list(db.session.scalars(select(Broker).order_by(Broker.name)))


def ticker_records() -> list[Ticker]:
    return list(db.session.scalars(select(Ticker).order_by(Ticker.symbol)))


def option_expirations() -> list[OptionExpiration]:
    statement = select(OptionExpiration).order_by(OptionExpiration.exercise_date)
    return list(db.session.scalars(statement))


def option_contracts() -> list[OptionContract]:
    statement = (
        select(OptionContract)
        .join(OptionContract.expiration)
        .options(
            joinedload(OptionContract.ticker_ref),
            joinedload(OptionContract.underlying_ticker_ref),
            joinedload(OptionContract.expiration),
        )
        .order_by(OptionExpiration.exercise_date, OptionContract.id)
    )
    return list(db.session.scalars(statement).unique())


def poll_interval_seconds() -> int:
    settings = db.session.get(AppSetting, 1)
    if settings is None:
        return DEFAULT_POLL_INTERVAL_SECONDS
    return settings.poll_interval_seconds


def rtd_service() -> RtdService:
    return cast(RtdService, current_app.extensions["rtd_service"])


def selected_filters() -> tuple[PositionKind | None, str | None, str]:
    raw_kind = request.args.get("position_kind", PositionKind.REAL.value)
    try:
        kind = None if raw_kind == "all" else PositionKind(raw_kind)
    except ValueError:
        kind, raw_kind = PositionKind.REAL, PositionKind.REAL.value
    broker = request.args.get("broker") or None
    return kind, broker, raw_kind


def allocation_chart_data(views: list[PositionView]) -> list[dict[str, object]]:
    """Dados para o gráfico de pizza de alocação por ativo, um por moeda
    (nunca misturando moedas — mesma convenção do resto do módulo)."""
    grouped: dict[str, list[PositionView]] = {}
    for view in views:
        if view.current_weight is not None:
            grouped.setdefault(view.position.currency, []).append(view)
    return [
        {
            "currency": currency,
            "labels": [view.position.ticker for view in group],
            "weights": [str(view.current_weight) for view in group],
        }
        for currency, group in sorted(grouped.items())
    ]


def stale_quote_rate(views: list[PositionView]) -> Decimal | None:
    """Fração de posições cuja cotação não está "online" (stale ou
    ausente). Item 4, Nível Operacional: "Taxa de stale quotes"."""
    if not views:
        return None
    not_online = sum(1 for view in views if view.quote_status != "online")
    return Decimal(not_online) / Decimal(len(views))


class TTLCache[T]:
    """A tiny in-process, thread-safe TTL cache for expensive read paths.

    Deliberately used to cache already-serialized (plain dict/JSON-ready)
    values rather than SQLAlchemy ORM objects: ORM instances are bound to a
    request-scoped session, so caching them across requests risks
    ``DetachedInstanceError`` once that session closes. A couple of seconds
    of staleness is acceptable here because RTD quotes already refresh on
    their own ~2s cadence (``RTD_REFRESH_SECONDS``).
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = Lock()
        self._store: dict[str, tuple[float, T]] = {}

    def get_or_set(self, key: str, factory: Callable[[], T]) -> T:
        now = time.monotonic()
        with self._lock:
            cached = self._store.get(key)
            if cached is not None and now - cached[0] < self._ttl_seconds:
                return cached[1]
        value = factory()
        with self._lock:
            self._store[key] = (now, value)
        return value
