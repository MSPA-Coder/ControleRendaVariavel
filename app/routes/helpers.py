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
    QuoteHistory,
    Side,
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


def stock_ticker_records() -> list[Ticker]:
    """Tickers de ações e índices, excluindo os que representam contratos de opção."""
    statement = (
        select(Ticker)
        .where(~Ticker.option_contract.has())
        .order_by(Ticker.symbol)
    )
    return list(db.session.scalars(statement))


BENCHMARK_SYMBOLS = ("BOVA11", "USDBRL=X")
"""Índices de referência oferecidos no comparador de evolução dos gráficos de
cotação e de performance. Cadastrados como tickers comuns (mesma convenção já
usada para o Ibovespa no Beta da Fase D, ver `quotes.html`), não como um tipo
de ativo à parte — por isso a busca é por símbolo, não por uma tabela nova."""


def benchmark_candidates(exclude_ticker_id: int | None = None) -> list[Ticker]:
    """Tickers de referência cadastrados, na ordem fixa de ``BENCHMARK_SYMBOLS``
    (não alfabética, para o dropdown ficar estável). ``exclude_ticker_id``
    evita oferecer comparar um ticker consigo mesmo."""
    statement = select(Ticker).where(Ticker.symbol.in_(BENCHMARK_SYMBOLS))
    by_symbol = {ticker.symbol: ticker for ticker in db.session.scalars(statement)}
    return [
        by_symbol[symbol]
        for symbol in BENCHMARK_SYMBOLS
        if symbol in by_symbol and by_symbol[symbol].id != exclude_ticker_id
    ]


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


def ticker_price_series(ticker_id: int) -> list[QuoteHistory]:
    """Série histórica de cotações de um ticker, em ordem cronológica
    (item 5/relatório de histórico de cotações; também usada pelos KPIs de
    risco da Fase D, que precisam dos mesmos retornos diários)."""
    statement = (
        select(QuoteHistory)
        .where(QuoteHistory.ticker_id == ticker_id)
        .order_by(QuoteHistory.recorded_date)
    )
    return list(db.session.scalars(statement))


def open_real_quantities_by_ticker() -> dict[int, Decimal]:
    """Quantidade líquida por ticker das posições REAIS ainda abertas.
    Usada pelos relatórios de risco (Fase D) e de performance mensal para
    a aproximação "posições atuais constantes no passado" (ver
    ``app.risk.portfolio_value_series``)."""
    statement = select(Position.ticker_id, Position.quantity, Position.side).where(
        Position.position_kind == PositionKind.REAL
    )
    totals: dict[int, Decimal] = {}
    for ticker_id, quantity, side in db.session.execute(statement):
        direction = Decimal("1") if side == Side.BUY else Decimal("-1")
        totals[ticker_id] = totals.get(ticker_id, Decimal("0")) + direction * quantity
    return {ticker_id: quantity for ticker_id, quantity in totals.items() if quantity != 0}


def poll_interval_seconds() -> int:
    settings = db.session.get(AppSetting, 1)
    if settings is None:
        return DEFAULT_POLL_INTERVAL_SECONDS
    return settings.poll_interval_seconds


def quote_stale_after_seconds() -> int:
    floor = poll_interval_seconds() * 2 + 5
    settings = db.session.get(AppSetting, 1)
    if settings is not None and settings.stale_alert_seconds is not None:
        return max(settings.stale_alert_seconds, floor)
    configured = int(current_app.config["RTD_STALE_AFTER_SECONDS"])
    return max(configured, floor)


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
