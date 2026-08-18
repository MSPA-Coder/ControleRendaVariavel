from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import cast

from flask import current_app, request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.collector_settings import DEFAULT_POLL_INTERVAL_SECONDS
from app.holdings_history import DividendEvent, HoldingEvent
from app.models import (
    AppSetting,
    Broker,
    Dividend,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionPositionMovement,
    Portfolio,
    Position,
    PositionLedgerArchive,
    PositionMovement,
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


def portfolio_records() -> list[Portfolio]:
    """Carteiras cadastradas, em ordem alfabética.

    Usada pelos formulários de posição, opção e transação (campo
    "Carteira" — a carteira de destino é escolhida no formulário, não
    derivada da moeda do ticker) e pelo filtro homônimo de Ações, Opções e
    Transações, que oferece as simuladas como escolha explícita. O que não
    as inclui é a opção "Todas" do filtro: ver ``positions_query``."""
    return list(db.session.scalars(select(Portfolio).order_by(Portfolio.name)))


def real_portfolio_records() -> list[Portfolio]:
    """Carteiras não-simuladas, em ordem alfabética.

    Opções do filtro de Carteira em Performance e Exposição (Análise), que
    nunca oferecem a carteira Simulada: essas páginas continuam excluindo-a
    incondicionalmente (decisão D3 do plano de carteiras), então não faz
    sentido oferecê-la como escolha."""
    return list(
        db.session.scalars(
            select(Portfolio).where(Portfolio.simulated.is_(False)).order_by(Portfolio.name)
        )
    )


def selected_filters() -> tuple[int | None, str | None, str]:
    """Filtro de Carteira (substitui o antigo dropdown "Posição") e filtro
    de corretora, lidos da query string.

    Ponto único de entrada do filtro: ``positions_query`` (Carteira/Ações e
    as páginas de Exposição) e a página de Transações partem todos daqui.
    Performance usa o mesmo valor bruto, mas adiciona por conta própria a
    exclusão incondicional da carteira Simulada (D3) — ver
    ``real_portfolio_records``.

    Sem parâmetro `portfolio_id`, ou com um valor que não corresponde ao id
    de nenhuma carteira, o filtro é "Todas" (``None``, sem filtro).
    """
    raw_portfolio = request.args.get("portfolio_id", "all")
    portfolio_id = int(raw_portfolio) if raw_portfolio.isdigit() else None
    if portfolio_id is None:
        raw_portfolio = "all"
    broker = request.args.get("broker") or None
    return portfolio_id, broker, raw_portfolio


def positions_query(
    portfolio_id: int | None = None,
    broker: str | None = None,
    *,
    group_by_broker: bool = True,
    exclude_simulated: bool = False,
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
            # `build_portfolio` lê `position.simulated` (via `portfolio_ref`)
            # para cada posição, para agrupar os totais por (moeda, simulada)
            # — sem eager load aqui, cada leitura seria uma consulta própria.
            joinedload(Position.portfolio_ref),
            # O extrato entra em uma consulta única para as posições da página:
            # a Carteira consulta o tamanho dele em toda linha, para decidir se
            # mostra o `+`, e sem isso seria uma consulta por posição.
            selectinload(Position.movements),
        )
        .order_by(*order_columns)
    )
    if portfolio_id is not None:
        statement = statement.where(Position.portfolio_id == portfolio_id)
    if portfolio_id is None or exclude_simulated:
        # "Todas" quer dizer todas as **reais**: carteira simulada é insight,
        # e misturá-la no conjunto padrão faria o número somado na tela se
        # ler como patrimônio. Para vê-la, escolhe-se ela no filtro.
        #
        # `exclude_simulated` força a exclusão mesmo com uma carteira
        # simulada escolhida na URL: é o que as páginas de Exposição
        # (Análise) usam, onde ela nunca entra (decisão D3). O resultado
        # fica vazio nesse caso, o que é seguro.
        statement = statement.join(Position.portfolio_ref).where(Portfolio.simulated.is_(False))
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


def portfolio_ticker_has_positions(portfolio_id: int, ticker_id: int) -> bool:
    """``True`` se existe posição (ação ou opção) desse ticker especificamente
    nessa carteira.

    Usado por ``app.routes.tables.remove_portfolio_ticker`` (CRUD de
    carteiras, WP2) para impedir remover a associação ``PortfolioTicker``
    enquanto a posição existir — diferente de ``ticker_has_holdings``, que
    verifica o ticker em qualquer carteira, aqui o par (carteira, ticker)
    precisa coincidir. Para opções, o ticker considerado é o da própria opção
    (``OptionContract.ticker_id``), não o do ativo-objeto: é ele que aparece
    associado à carteira em ``portfolio_tickers``."""
    has_position = db.session.scalar(
        select(Position.id)
        .where(Position.ticker_id == ticker_id, Position.portfolio_id == portfolio_id)
        .limit(1)
    )
    if has_position is not None:
        return True
    has_option_position = db.session.scalar(
        select(OptionPosition.id)
        .join(OptionContract, OptionContract.id == OptionPosition.contract_id)
        .where(
            OptionContract.ticker_id == ticker_id,
            OptionPosition.portfolio_id == portfolio_id,
        )
        .limit(1)
    )
    return has_option_position is not None


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
            Ticker.is_benchmark,
            func.min(Position.opened_on).label("start_date"),
        )
        .join(Ticker, Ticker.id == Position.ticker_id)
        .group_by(Position.ticker_id, Ticker.symbol, Ticker.market, Ticker.is_benchmark)
    ).all()
    targets: dict[int, tuple[TickerImportTarget, date]] = {
        row.ticker_id: (
            TickerImportTarget(row.ticker_id, row.symbol, row.market, row.is_benchmark),
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
        targets[row.id] = (
            TickerImportTarget(row.id, row.symbol, row.market, is_benchmark=True),
            benchmark_start,
        )

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
    statement = (
        select(Position.ticker_id, Position.quantity, Position.side)
        .join(Position.portfolio_ref)
        .where(Portfolio.simulated.is_(False))
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
    statement = (
        select(Position.ticker_id, Position.quantity, Position.average_cost)
        .join(Position.portfolio_ref)
        .where(Portfolio.simulated.is_(False))
    )
    totals: dict[int, Decimal] = {}
    for ticker_id, quantity, average_cost in db.session.execute(statement):
        accumulate_invested_amount(ticker_id, quantity, average_cost, totals)
    return totals


def position_movement_events(
    portfolio_id: int | None = None, broker: str | None = None
) -> list[HoldingEvent]:
    """Extrato de posições REAIS — ações e opções — como eventos de
    quantidade e fluxo assinados, insumo de ``app.holdings_history`` para o
    TWR encadeado (ver "Performance mensal" em ``docs/planilha-acoes.md``).

    Os filtros são os mesmos de ``app.routes.performance`` — carteira,
    corretora e exclusão incondicional da carteira Simulada (decisão D3) —
    replicados aqui porque a rota não acessa persistência diretamente.
    Posição simulada já não gera ``PositionMovement``/``OptionPositionMovement``
    (``discard_simulation_history``), então a exclusão de
    ``Portfolio.simulated`` é redundante com esse fato; mesmo assim ela fica
    explícita, para o relatório financeiro não depender de um efeito
    colateral de outra função como única garantia.

    Três consultas — ações vivas, opções vivas e o arquivo das já encerradas
    (``PositionLedgerArchive``) —, nunca uma por posição (sem N+1). O arquivo
    entra porque encerrar uma posição apaga o extrato dela em cascata: sem
    ele, o relatório mediria apenas os ativos que continuaram na carteira. O sinal vem do ``side`` da POSIÇÃO, que não existe no
    movimento, e é aplicado a ``resulting_quantity``. Como o saldo
    resultante já está gravado em cada linha do extrato, os quatro tipos de
    movimento (``PositionMovementKind``) são lidos pela mesma fórmula, sem
    examinar ``kind``: abertura, aumento, baixa parcial e ajuste só diferem
    no saldo que deixam.

    O ``price`` do movimento NÃO é lido aqui de propósito. O fluxo do TWR é
    avaliado a preço de mercado da data, não ao preço lançado — ver o
    docstring de ``app.holdings_history.portfolio_flow_series`` para o
    motivo (``opened_on`` de posição antiga é a data do cadastro, não da
    compra; ``quote_history`` guarda fechamento ajustado enquanto o custo
    médio é nominal).

    O ticker de uma opção é o do CONTRATO (``OptionContract.ticker_id``),
    nunca o do ativo-objeto (``underlying_ticker_id``): é o contrato que tem
    preço e é negociado — mesma regra documentada em
    ``OptionPosition.currency``. ``position_key`` carrega a origem
    (``("stock", id)`` / ``("option", id)``) porque ``Position`` e
    ``OptionPosition`` têm sequências de id independentes e o mesmo inteiro
    identifica as duas ao mesmo tempo — a mesma armadilha já documentada em
    ``Transaction.source_position_id``.
    """
    stock_statement = (
        select(
            PositionMovement.occurred_on,
            Position.id,
            Position.ticker_id,
            Position.side,
            PositionMovement.resulting_quantity,
        )
        .join(PositionMovement.position)
        .join(Position.broker_ref)
        .join(Position.portfolio_ref)
        .where(Portfolio.simulated.is_(False))
        .order_by(PositionMovement.occurred_on, PositionMovement.id)
    )
    if portfolio_id is not None:
        stock_statement = stock_statement.where(Position.portfolio_id == portfolio_id)
    if broker:
        stock_statement = stock_statement.where(Broker.name == broker)

    option_statement = (
        select(
            OptionPositionMovement.occurred_on,
            OptionPosition.id,
            OptionContract.ticker_id,
            OptionPosition.side,
            OptionPositionMovement.resulting_quantity,
        )
        .join(OptionPositionMovement.position)
        .join(OptionPosition.contract)
        .join(OptionPosition.broker_ref)
        .join(OptionPosition.portfolio_ref)
        .where(Portfolio.simulated.is_(False))
        .order_by(OptionPositionMovement.occurred_on, OptionPositionMovement.id)
    )
    if portfolio_id is not None:
        option_statement = option_statement.where(OptionPosition.portfolio_id == portfolio_id)
    if broker:
        option_statement = option_statement.where(Broker.name == broker)

    events: list[HoldingEvent] = []
    for (
        occurred_on,
        position_id,
        ticker_id,
        side,
        resulting_quantity,
    ) in db.session.execute(stock_statement):
        sign = Decimal("1") if side == Side.BUY else Decimal("-1")
        events.append(
            HoldingEvent(
                occurred_on=occurred_on,
                ticker_id=ticker_id,
                resulting_signed_quantity=sign * resulting_quantity,
                position_key=("stock", position_id),
            )
        )
    for (
        occurred_on,
        position_id,
        ticker_id,
        side,
        resulting_quantity,
    ) in db.session.execute(option_statement):
        sign = Decimal("1") if side == Side.BUY else Decimal("-1")
        events.append(
            HoldingEvent(
                occurred_on=occurred_on,
                ticker_id=ticker_id,
                resulting_signed_quantity=sign * resulting_quantity,
                position_key=("option", position_id),
            )
        )
    # Posicoes ja encerradas nao tem mais extrato (a exclusao o leva em
    # cascata); o que sobrou delas esta no arquivo. Sem esta terceira
    # consulta o relatorio mediria so os ativos que continuaram na carteira
    # -- vies de sobrevivencia. Ver `app.position_ledger`.
    archive_statement = (
        select(
            PositionLedgerArchive.occurred_on,
            PositionLedgerArchive.ticker_id,
            PositionLedgerArchive.instrument,
            PositionLedgerArchive.source_position_id,
            PositionLedgerArchive.resulting_signed_quantity,
        )
        .join(Broker, Broker.id == PositionLedgerArchive.broker_id)
        .join(Portfolio, Portfolio.id == PositionLedgerArchive.portfolio_id)
        .where(Portfolio.simulated.is_(False))
        .order_by(PositionLedgerArchive.occurred_on, PositionLedgerArchive.id)
    )
    if portfolio_id is not None:
        archive_statement = archive_statement.where(
            PositionLedgerArchive.portfolio_id == portfolio_id
        )
    if broker:
        archive_statement = archive_statement.where(Broker.name == broker)
    for (
        occurred_on,
        ticker_id,
        instrument,
        source_position_id,
        resulting_signed_quantity,
    ) in db.session.execute(archive_statement):
        events.append(
            HoldingEvent(
                occurred_on=occurred_on,
                ticker_id=ticker_id,
                # O sinal ja foi aplicado na gravacao do arquivo.
                resulting_signed_quantity=resulting_signed_quantity,
                position_key=(instrument, source_position_id),
            )
        )

    events.sort(key=lambda event: event.occurred_on)
    return events


def dividend_events(ticker_ids: Iterable[int]) -> list[DividendEvent]:
    """Rendas dos tickers pedidos — dividendo, JCP e aluguel de ações —, em
    ordem cronológica, insumo de ``app.holdings_history.prorate_dividends``.

    As três entram no retorno da carteira, e por isso nenhuma é filtrada
    aqui: desde que ``quote_history`` passou a gravar o ``close`` nominal
    (ver ``app.quote_history_import``), o preço não embute mais nenhuma
    delas, e deixar qualquer uma de fora subestimaria o retorno. ``kind``
    viaja junto para o relatório poder dizer quanto cada uma rendeu.

    Sem filtro de corretora: ``Dividend`` não tem ``portfolio_id``, e
    atribuí-lo à corretora do lançamento inflaria ou apagaria o crédito
    dependendo de qual corretora detém o ativo no recorte filtrado. O
    rateio pela quantidade efetivamente detida (``prorate_dividends``, a
    partir das duas linhas do tempo — filtrada e total — de
    ``position_movement_events``) resolve carteira e corretora com a mesma
    regra já documentada em ``open_real_cost_basis_by_ticker``: "um provento
    é um evento do ativo, não da corretora que o pagou". Lista vazia devolve
    lista vazia sem consultar o banco — evita uma query com ``IN ()`` vazio
    quando o relatório não tem nenhum ticker com posição no recorte.
    """
    ids = list(ticker_ids)
    if not ids:
        return []
    statement = (
        select(Dividend.payment_date, Dividend.ticker_id, Dividend.amount, Dividend.kind)
        .where(Dividend.ticker_id.in_(ids))
        .order_by(Dividend.payment_date)
    )
    return [
        DividendEvent(
            payment_date=payment_date, ticker_id=ticker_id, amount=amount, kind=str(kind)
        )
        for payment_date, ticker_id, amount, kind in db.session.execute(statement)
    ]


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


def rtd_service_state() -> tuple[bool, bool, str]:
    """``(ligado, disponível, status)`` do coletor, tolerante a host offline.

    O controlador RTD é um sistema externo: quando não responde, a tela mostra
    o coletor como indisponível em vez de quebrar. Nenhuma operação de
    cadastro depende dele.
    """
    service = rtd_service()
    try:
        return service.is_running, service.available, service.status
    except (OSError, RuntimeError):
        return False, False, "unavailable"


def exposure_chart_data(
    entries: Iterable[tuple[str, str, Decimal | None, Decimal | None]],
) -> list[dict[str, object]]:
    """Dados para os gráficos de exposição, um bloco por moeda.

    ``entries`` são quádruplas ``(moeda, rótulo, peso, valor)``. Moedas nunca são
    misturadas, seguindo a mesma convenção do resto do módulo: somar BRL e
    USD sem taxa de câmbio produziria um número sem significado. Entradas
    sem peso (posição sem cotação) ficam de fora do gráfico.

    O formato de saída é consumido por ``allocation-chart.js`` nas três
    páginas de Análise (por ativo, por corretora e por mercado).
    """
    grouped: dict[str, list[tuple[str, Decimal, Decimal]]] = {}
    for currency, label, weight, value in entries:
        if weight is not None and value is not None:
            grouped.setdefault(currency, []).append((label, weight, value))
    return [
        {
            "currency": currency,
            "labels": [label for label, _, _ in group],
            # `weights` é string porque o gráfico JS a consome via `tojson`.
            "weights": [str(weight) for _, weight, _ in group],
            # `weight_values` mantém o Decimal cru (fração, ex.: 0.1234) para
            # a lista textual usar o filtro `percent` — ver invariante
            # financeira em AGENTS.md (percentual sempre via Decimal, nunca
            # `|float`).
            "weight_values": [weight for _, weight, _ in group],
            "values": [str(value) for _, _, value in group],
            "value_values": [value for _, _, value in group],
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
        (
            view.position.currency,
            view.position.ticker,
            view.current_weight,
            abs(view.metrics.unwind_value) if view.metrics is not None else None,
        )
        for view in views
    )


def broker_exposure_chart_data(broker_groups: list[BrokerGroup]) -> list[dict[str, object]]:
    """Exposição por corretora."""
    return exposure_chart_data(
        (group.currency, group.broker, group.current_weight, group.current_total)
        for group in broker_groups
    )


def market_exposure_chart_data(market_groups: list[MarketGroup]) -> list[dict[str, object]]:
    """Exposição por mercado."""
    return exposure_chart_data(
        (group.currency, group.market.value, group.current_weight, group.current_total)
        for group in market_groups
    )


def converted_exposure_chart_data(
    entries: Iterable[tuple[str, str, Decimal]], usd_brl_rate: Decimal | None
) -> dict[str, object] | None:
    """Exposição consolidada em USD, agrupada pelo rótulo do gráfico.

    A cotação de referência é BRL por USD (ticker ``USDBRL=X``). Valores em
    BRL são divididos por ela; valores já em USD permanecem inalterados.
    Sem a taxa ou sem mais de uma moeda, a tela mantém apenas as visões
    separadas para não inventar uma soma entre moedas.
    """
    entry_list = list(entries)
    if (
        len({currency for currency, _, _ in entry_list}) < 2
        or usd_brl_rate is None
        or usd_brl_rate <= 0
    ):
        return None
    values_by_label: dict[str, Decimal] = {}
    for currency, label, raw_value in entry_list:
        value = raw_value / usd_brl_rate if currency == "BRL" else raw_value
        values_by_label[label] = values_by_label.get(label, Decimal("0")) + value
    total = sum(values_by_label.values(), Decimal("0"))
    if not total:
        return None
    return {
        "currency": "USD",
        "labels": list(values_by_label),
        "weights": [str(value / total) for value in values_by_label.values()],
        "weight_values": [value / total for value in values_by_label.values()],
        "values": [str(value) for value in values_by_label.values()],
        "value_values": list(values_by_label.values()),
        "converted": True,
    }


def converted_allocation_chart_data(
    views: list[PositionView], usd_brl_rate: Decimal | None
) -> dict[str, object] | None:
    return converted_exposure_chart_data(
        (
            (
                view.position.currency,
                view.position.ticker,
                abs(view.metrics.unwind_value),
            )
            for view in views
            if view.metrics is not None
        ),
        usd_brl_rate,
    )


def converted_broker_exposure_chart_data(
    broker_groups: list[BrokerGroup], usd_brl_rate: Decimal | None
) -> dict[str, object] | None:
    return converted_exposure_chart_data(
        ((group.currency, group.broker, group.current_total) for group in broker_groups),
        usd_brl_rate,
    )


def converted_market_exposure_chart_data(
    market_groups: list[MarketGroup], usd_brl_rate: Decimal | None
) -> dict[str, object] | None:
    return converted_exposure_chart_data(
        ((group.currency, group.market.value, group.current_total) for group in market_groups),
        usd_brl_rate,
    )


def latest_usd_brl_quote() -> Decimal | None:
    """Última cotação manual/importada de USDBRL=X, em BRL por USD."""
    return db.session.scalar(
        select(QuoteHistory.price)
        .join(Ticker, Ticker.id == QuoteHistory.ticker_id)
        .where(Ticker.symbol == "USDBRL=X")
        .order_by(QuoteHistory.recorded_date.desc(), QuoteHistory.recorded_at.desc())
        .limit(1)
    )
