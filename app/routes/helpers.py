from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from threading import Lock
from typing import cast

from flask import current_app, request
from sqlalchemy import func, select
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
from app.quote_history_import import TickerImportTarget
from app.rtd_service import RtdService

DEFAULT_BENCHMARK_IMPORT_LOOKBACK_DAYS = 730
"""Janela usada para a primeira importação de um ticker de referência
(``Ticker.is_benchmark``) quando ainda não existe nenhuma posição
cadastrada no app (portanto sem uma data real para ancorar o início do
histórico) — ver ``quote_update_targets``."""


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


def investable_ticker_records() -> list[Ticker]:
    """Tickers elegíveis para Posições, Transações, Proventos e Contratos de
    Opção (como ativo ou como subjacente): exclui os marcados como
    referência de comparação (``Ticker.is_benchmark``), que não
    representam algo que se compra ou vende na carteira — ver
    ``benchmark_candidates``."""
    statement = select(Ticker).where(Ticker.is_benchmark.is_(False)).order_by(Ticker.symbol)
    return list(db.session.scalars(statement))


def ticker_has_holdings(ticker_id: int) -> bool:
    """``True`` se o ticker já está referenciado por alguma posição (real
    ou hipotética) ou contrato de opção (como opção ou como subjacente).

    Usado por ``app.routes.tables`` para impedir marcar um ticker como
    referência de comparação (``Ticker.is_benchmark``) enquanto ele ainda
    estiver em uso como ativo — as duas coisas são mutuamente exclusivas
    (ver também ``investable_ticker_records``, que faz a exclusão inversa
    nos formulários que criam esse tipo de vínculo)."""
    has_position = db.session.scalar(
        select(Position.id).where(Position.ticker_id == ticker_id).limit(1)
    )
    if has_position is not None:
        return True
    has_option_link = db.session.scalar(
        select(OptionContract.id)
        .where(
            (OptionContract.ticker_id == ticker_id)
            | (OptionContract.underlying_ticker_id == ticker_id)
        )
        .limit(1)
    )
    return has_option_link is not None


def benchmark_candidates(exclude_ticker_id: int | None = None) -> list[Ticker]:
    """Tickers marcados como referência de comparação (``Ticker.is_benchmark``),
    em ordem alfabética, oferecidos nos comparadores de evolução dos
    gráficos de Cotações e Performance. ``exclude_ticker_id`` evita
    oferecer comparar um ticker consigo mesmo."""
    statement = select(Ticker).where(Ticker.is_benchmark.is_(True)).order_by(Ticker.symbol)
    return [
        ticker for ticker in db.session.scalars(statement) if ticker.id != exclude_ticker_id
    ]


def quote_update_targets() -> list[tuple[TickerImportTarget, date]]:
    """Tickers e data inicial para a atualização de histórico "desde a
    posição" (comando ``flask import-position-history`` e rota
    ``/quotes/import-position-history``).

    Reúne dois grupos, sem duplicar um ticker que esteja nos dois:

    - Tickers com ao menos uma posição (real ou hipotética): a partir da
      data de abertura mais antiga desse ticker especificamente (mesmo
      critério de sempre).
    - Tickers de referência (``Ticker.is_benchmark``): a partir da data de
      abertura mais antiga entre TODAS as posições existentes, para que o
      histórico do benchmark cubra qualquer comparação possível — mesmo
      sem ter, ele próprio, uma posição. É isso que evita a necessidade de
      abrir uma posição "fantasma" só para manter a cotação atualizada
      (ver discussão com o usuário). Sem nenhuma posição cadastrada ainda,
      usa ``DEFAULT_BENCHMARK_IMPORT_LOOKBACK_DAYS`` para já deixar
      histórico disponível antes da primeira compra.
    """
    position_rows = db.session.execute(
        select(
            Position.ticker_id,
            Ticker.symbol,
            Ticker.market,
            func.min(Position.opened_on).label("start_date"),
        )
        .join(Ticker, Ticker.id == Position.ticker_id)
        .group_by(Position.ticker_id, Ticker.symbol, Ticker.market)
    ).all()
    targets: dict[int, tuple[TickerImportTarget, date]] = {
        row.ticker_id: (
            TickerImportTarget(row.ticker_id, row.symbol, row.market),
            row.start_date,
        )
        for row in position_rows
    }

    earliest_position_start = db.session.scalar(select(func.min(Position.opened_on)))
    benchmark_start = earliest_position_start or (
        date.today() - timedelta(days=DEFAULT_BENCHMARK_IMPORT_LOOKBACK_DAYS)
    )
    benchmark_rows = db.session.execute(
        select(Ticker.id, Ticker.symbol, Ticker.market).where(Ticker.is_benchmark.is_(True))
    ).all()
    for row in benchmark_rows:
        if row.id in targets:
            # Já coberto por uma posição própria, com data mais específica.
            continue
        targets[row.id] = (TickerImportTarget(row.id, row.symbol, row.market), benchmark_start)

    return sorted(targets.values(), key=lambda pair: pair[0].symbol)


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


def ticker_position_start_date(ticker_id: int) -> date | None:
    """Data de abertura mais antiga entre as posições (reais ou
    hipotéticas) de um ticker, ou ``None`` se ele nunca foi usado em uma
    posição. Usada para ancorar o comparador de índice em "desde que a
    posição foi aberta" em vez de todo o histórico de cotações disponível
    (que costuma remontar a muito antes da compra) — ver
    ``app.routes.helpers.benchmark_candidates``."""
    return db.session.scalar(
        select(func.min(Position.opened_on)).where(Position.ticker_id == ticker_id)
    )


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
