from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Lock
from typing import cast

from flask import current_app, request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
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
from app.portfolio import BrokerGroup, MarketGroup, PositionView
from app.quote_history_import import TickerImportTarget
from app.rtd_service import RtdService

DEFAULT_BENCHMARK_IMPORT_LOOKBACK_DAYS = 730
"""Janela usada para a primeira importação de um ticker de referência
(``Ticker.is_benchmark``) quando ainda não existe nenhuma posição
cadastrada no app (portanto sem uma data real para ancorar o início do
histórico) — ver ``quote_update_targets``."""


def positions_query(
    position_kind: PositionKind | None = None,
    broker: str | None = None,
    *,
    group_by_broker: bool = True,
) -> list[Position]:
    order_columns = (
        (Ticker.currency, Broker.name, Ticker.symbol, Position.opened_on)
        if group_by_broker
        else (Ticker.currency, Ticker.symbol, Broker.name, Position.opened_on)
    )
    statement = (
        select(Position)
        .join(Position.broker_ref)
        .join(Position.ticker_ref)
        .options(
            joinedload(Position.quote),
            joinedload(Position.broker_ref),
            joinedload(Position.ticker_ref),
        )
        .order_by(*order_columns)
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
      abrir uma posição "fantasma" só para manter a cotação atualizada.
      Sem nenhuma posição cadastrada ainda, usa
      ``DEFAULT_BENCHMARK_IMPORT_LOOKBACK_DAYS`` para já deixar histórico
      disponível antes da primeira compra.
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


def quote_update_target_tickers() -> list[TickerImportTarget]:
    """Tickers elegíveis para atualização de cotação: com posição (real ou
    hipotética) ou marcados como referência de comparação (Ticker.is_benchmark).
    Mesmo critério de ``quote_update_targets``, mas sem a data de início por
    ticker — usado pela atualização "diária" que sempre usa um período
    explícito (start_date/end_date) informado no formulário.
    """
    return [target for target, _ in quote_update_targets()]


def upsert_quote_history(entries: Iterable[tuple[int, Decimal, date, datetime]]) -> None:
    """Grava um snapshot de cotação por (ticker, dia).

    ``entries`` são tuplas ``(ticker_id, preço, data, instante)``. Um segundo
    lançamento para o mesmo ticker no mesmo dia substitui o anterior em vez
    de duplicar — a unique constraint em ``quote_history`` também impede a
    duplicata. É o mesmo caminho usado pelo lançamento manual, pela
    importação diária, pela importação "desde a posição" e pelo coletor RTD.

    Não faz ``commit``: quem inicia a operação de escrita é dono do limite
    transacional.
    """
    for ticker_id, price, recorded_date, recorded_at in entries:
        statement = insert(QuoteHistory).values(
            ticker_id=ticker_id,
            price=price,
            recorded_date=recorded_date,
            recorded_at=recorded_at,
        )
        db.session.execute(
            statement.on_conflict_do_update(
                index_elements=[QuoteHistory.ticker_id, QuoteHistory.recorded_date],
                set_={
                    "price": statement.excluded.price,
                    "recorded_at": statement.excluded.recorded_at,
                },
            )
        )


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
    (também usada pelos KPIs de risco, que precisam dos mesmos retornos
    diários)."""
    statement = (
        select(QuoteHistory)
        .where(QuoteHistory.ticker_id == ticker_id)
        .order_by(QuoteHistory.recorded_date)
    )
    return list(db.session.scalars(statement))


def price_series_by_ticker(ticker_ids: Iterable[int]) -> dict[int, list[tuple[date, Decimal]]]:
    """Séries históricas de vários tickers em uma única consulta.

    Equivale a chamar ``ticker_price_series`` por ticker, mas sem emitir uma
    query por ativo da carteira (N+1) — o relatório de performance mensal
    precisa de todas as séries de uma vez. Tickers sem histórico aparecem
    com lista vazia, para que quem chama não precise tratar chave ausente.
    """
    ids = list(ticker_ids)
    series: dict[int, list[tuple[date, Decimal]]] = {ticker_id: [] for ticker_id in ids}
    if not ids:
        return series
    rows = db.session.execute(
        select(QuoteHistory.ticker_id, QuoteHistory.recorded_date, QuoteHistory.price)
        .where(QuoteHistory.ticker_id.in_(ids))
        .order_by(QuoteHistory.ticker_id, QuoteHistory.recorded_date)
    )
    for ticker_id, recorded_date, price in rows:
        series[ticker_id].append((recorded_date, price))
    return series


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
    Usada pelos relatórios de risco e de performance mensal para
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


def accumulate_signed_quantity(
    ticker_id: int,
    quantity: Decimal,
    side: Side,
    opened_on: date,
    quantities_by_ticker: dict[int, Decimal],
    opened_on_by_ticker: dict[int, date],
) -> None:
    """Acumula, em ``quantities_by_ticker``, a quantidade líquida de uma
    posição (positiva para compra, negativa para venda) e, em
    ``opened_on_by_ticker``, a data de abertura mais antiga já vista para
    o ticker. Mesma matemática de sinal de ``open_real_quantities_by_ticker``,
    mas aplicada linha a linha para que o relatório de performance mensal
    (``app.routes.performance``) possa combinar, no mesmo par de
    acumuladores, posições de fontes diferentes — ações e opções, reais e
    hipotéticas — sem duplicar a lógica de sinal em cada laço."""
    direction = Decimal("1") if side == Side.BUY else Decimal("-1")
    quantities_by_ticker[ticker_id] = quantities_by_ticker.get(ticker_id, Decimal("0")) + (
        direction * quantity
    )
    earliest = opened_on_by_ticker.get(ticker_id)
    if earliest is None or opened_on < earliest:
        opened_on_by_ticker[ticker_id] = opened_on


def accumulate_invested_amount(
    ticker_id: int,
    quantity: Decimal,
    average_cost: Decimal,
    invested_amount_by_ticker: dict[int, Decimal],
) -> None:
    """Acumula, em ``invested_amount_by_ticker``, o valor investido
    (quantidade × custo médio) de uma posição. Usada tanto pelo
    relatório de performance mensal (curva hipotética do comparador de
    benchmark, restrita a ações — ver ``app.routes.performance``) quanto
    por ``open_real_cost_basis_by_ticker`` (base de custo dos
    proventos)."""
    invested_amount_by_ticker[ticker_id] = (
        invested_amount_by_ticker.get(ticker_id, Decimal("0")) + quantity * average_cost
    )


def open_real_cost_basis_by_ticker() -> dict[int, Decimal]:
    """Custo de aquisição atual por ticker: soma de quantidade × custo
    médio das posições REAIS ainda abertas (base de cálculo do
    "yield on cost" no Relatório de Proventos).

    Deliberadamente não filtra por corretora nem por outros filtros de
    página: representa a base de custo total do ativo hoje, não uma
    fatia por corretora — um provento é um evento do ativo, não da
    corretora que o pagou. Tickers sem posição REAL aberta simplesmente
    não aparecem no mapeamento resultante."""
    statement = select(Position.ticker_id, Position.quantity, Position.average_cost).where(
        Position.position_kind == PositionKind.REAL
    )
    totals: dict[int, Decimal] = {}
    for ticker_id, quantity, average_cost in db.session.execute(statement):
        accumulate_invested_amount(ticker_id, quantity, average_cost, totals)
    return totals


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


def is_htmx_request() -> bool:
    """``True`` quando a requisição veio do HTMX.

    É um sinal de **apresentação**: decide se a resposta é a página inteira
    ou apenas o fragmento atualizado. Nunca deve ser usado como prova de
    autenticação, autorização ou origem confiável — o cabeçalho é definido
    pelo cliente e pode ser forjado. Autorização continua sendo aplicada no
    servidor, do mesmo jeito para os dois tipos de requisição.
    """
    return request.headers.get("HX-Request") == "true"


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


def exposure_chart_data(
    entries: Iterable[tuple[str, str, Decimal | None]],
) -> list[dict[str, object]]:
    """Dados para os gráficos de exposição, um bloco por moeda.

    ``entries`` são triplas ``(moeda, rótulo, peso)``. Moedas nunca são
    misturadas, seguindo a mesma convenção do resto do módulo: somar BRL e
    USD sem taxa de câmbio produziria um número sem significado. Entradas
    sem peso (posição sem cotação) ficam de fora do gráfico.

    O formato de saída é consumido por ``allocation-chart.js`` nas três
    páginas de Análise (por ativo, por corretora e por mercado).
    """
    grouped: dict[str, list[tuple[str, Decimal]]] = {}
    for currency, label, weight in entries:
        if weight is not None:
            grouped.setdefault(currency, []).append((label, weight))
    return [
        {
            "currency": currency,
            "labels": [label for label, _ in group],
            # `weights` é string porque o gráfico JS a consome via `tojson`.
            "weights": [str(weight) for _, weight in group],
            # `weight_values` mantém o Decimal cru (fração, ex.: 0.1234) para
            # a lista textual usar o filtro `percent` — ver invariante
            # financeira em AGENTS.md (percentual sempre via Decimal, nunca
            # `|float`).
            "weight_values": [weight for _, weight in group],
        }
        for currency, group in sorted(grouped.items())
    ]


def exposure_group_rows(
    groups: Sequence[BrokerGroup] | Sequence[MarketGroup],
    label: Callable[[BrokerGroup], str] | Callable[[MarketGroup], str],
) -> list[dict[str, object]]:
    """Linhas da tabela de grupos das paginas de Exposicao.

    O rotulo e resolvido aqui porque corretora e mercado o obtem de
    atributos diferentes; com ele pronto, as tres paginas compartilham um
    unico fragmento em vez de um template por recorte.
    """
    return [
        {
            "label": label(group),  # type: ignore[arg-type]
            "currency": group.currency,
            "current_total": group.current_total,
            "current_weight": group.current_weight,
        }
        for group in groups
    ]


def allocation_chart_data(views: list[PositionView]) -> list[dict[str, object]]:
    """Exposição por ativo."""
    return exposure_chart_data(
        (view.position.currency, view.position.ticker, view.current_weight) for view in views
    )


def broker_exposure_chart_data(broker_groups: list[BrokerGroup]) -> list[dict[str, object]]:
    """Exposição por corretora."""
    return exposure_chart_data(
        (group.currency, group.broker, group.current_weight) for group in broker_groups
    )


def market_exposure_chart_data(market_groups: list[MarketGroup]) -> list[dict[str, object]]:
    """Exposição por mercado."""
    return exposure_chart_data(
        (group.currency, group.market.value, group.current_weight) for group in market_groups
    )


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
