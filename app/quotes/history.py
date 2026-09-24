"""Histórico de cotações: o que importar e como gravar.

Vale para todos os caminhos que gravam cotação -- a tela de cotações, a CLI
(`flask import-position-history`) e o coletor RTD. Mora fora de `app.routes`
porque o coletor e a CLI não são camada web: até 24/09/2026 os dois
importavam estas funções de `app.routes.helpers`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Date, func, literal, select
from sqlalchemy.dialects.postgresql import insert

from app import db
from app.models import (
    OptionContract,
    OptionPosition,
    Position,
    PositionLedgerArchive,
    QuoteHistory,
    Ticker,
    Transaction,
)
from app.quotes.history_import import TickerImportTarget

DEFAULT_BENCHMARK_IMPORT_LOOKBACK_DAYS = 730
"""Janela usada para a primeira importação de um ticker de referência
(``Ticker.is_benchmark``) quando ainda não existe nenhuma posição
cadastrada no app (portanto sem uma data real para ancorar o início do
histórico) — ver ``quote_update_targets``."""


def _held_periods(today: date) -> dict[int, tuple[date, date]]:
    """Primeiro e último dia em que cada ticker esteve na carteira.

    Quatro fontes, porque nenhuma sozinha conta a história inteira:

    - posições de ação e de opção abertas (real ou hipotética): da abertura
      até hoje;
    - operações (``Transaction``): da abertura até o encerramento, ou até
      hoje se ainda aberta. É a única fonte que guarda posições já
      encerradas com a data de encerramento;
    - o arquivo do extrato (``PositionLedgerArchive``): cada evento de uma
      posição já apagada da carteira.

    O último dia é hoje se alguma fonte diz que o ticker continua detido;
    senão, o último encerramento. O intervalo entre um encerramento e uma
    reabertura entra junto: não é necessário, mas também não atrapalha.
    """
    rows = (
        select(Position.ticker_id, Position.opened_on, literal(None, Date)),
        select(OptionContract.ticker_id, OptionPosition.opened_on, literal(None, Date)).join(
            OptionContract, OptionPosition.contract_id == OptionContract.id
        ),
        select(
            func.coalesce(Transaction.ticker_id, OptionContract.ticker_id),
            Transaction.opened_on,
            func.coalesce(Transaction.closed_on, today),
        ).outerjoin(OptionContract, Transaction.option_contract_id == OptionContract.id),
        select(
            PositionLedgerArchive.ticker_id,
            PositionLedgerArchive.occurred_on,
            PositionLedgerArchive.occurred_on,
        ),
    )
    periods: dict[int, tuple[date, date]] = {}
    for statement in rows:
        for ticker_id, start, end in db.session.execute(statement):
            end = today if end is None else end
            previous = periods.get(ticker_id)
            if previous is not None:
                start, end = min(start, previous[0]), max(end, previous[1])
            periods[ticker_id] = (start, end)
    return periods


def quote_update_targets() -> list[tuple[TickerImportTarget, date, date]]:
    """Ativos e período para a atualização de histórico "desde a posição"
    (comando ``flask import-position-history`` e rota
    ``/quotes/import-position-history``).

    - Ticker que esteve na carteira: do primeiro ao último dia em que foi
      detido (``_held_periods``). Até 23/09/2026 só as posições ABERTAS
      contavam: um ativo encerrado (HODL11) deixava de ser importado, e um
      com operação encerrada antes da posição atual (CGC) começava tarde.
    - Ticker de referência (``Ticker.is_benchmark``): da abertura mais
      antiga da carteira até hoje, para cobrir qualquer comparação possível
      sem uma posição "fantasma". Sem nenhuma posição, usa
      ``DEFAULT_BENCHMARK_IMPORT_LOOKBACK_DAYS``.
    """
    today = date.today()
    periods = _held_periods(today)
    benchmark_start = min(
        (start for start, _ in periods.values()),
        default=today - timedelta(days=DEFAULT_BENCHMARK_IMPORT_LOOKBACK_DAYS),
    )
    tickers = db.session.execute(
        select(Ticker.id, Ticker.symbol, Ticker.market, Ticker.is_benchmark).where(
            Ticker.id.in_(list(periods)) | Ticker.is_benchmark.is_(True)
        )
    ).all()
    targets = []
    for row in tickers:
        target = TickerImportTarget(row.id, row.symbol, row.market, row.is_benchmark)
        if row.is_benchmark:
            # Referência cobre a carteira inteira, mesmo que tenha sido detida.
            start = min(benchmark_start, periods.get(row.id, (benchmark_start,))[0])
            targets.append((target, start, today))
        else:
            targets.append((target, *periods[row.id]))
    return sorted(targets, key=lambda item: item[0].symbol)


def quote_update_target_tickers() -> list[TickerImportTarget]:
    """Tickers da atualização "diária", que usa o período do formulário:
    os ainda detidos e os de referência. Um ticker já encerrado não recebe
    cotação posterior ao encerramento -- a carteira não precisa dela.
    """
    today = date.today()
    return [target for target, _start, end in quote_update_targets() if end == today]


#: Linhas por instrução em ``upsert_quote_history``. Quatro parâmetros por
#: linha deixam cada lote bem abaixo do teto de 65535 parâmetros do protocolo
#: do PostgreSQL.
QUOTE_HISTORY_UPSERT_BATCH_SIZE = 1000


def upsert_quote_history(entries: Iterable[tuple[int, Decimal, date, datetime]]) -> None:
    """Grava um snapshot de cotação por (ticker, dia).

    ``entries`` são tuplas ``(ticker_id, preço, data, instante)``. Um segundo
    lançamento para o mesmo ticker no mesmo dia substitui o anterior em vez
    de duplicar — a unique constraint em ``quote_history`` também impede a
    duplicata. É o mesmo caminho usado pelo lançamento manual, pela
    importação diária, pela importação "desde a posição" e pelo coletor RTD.

    Grava em lotes de ``QUOTE_HISTORY_UPSERT_BATCH_SIZE`` linhas por
    instrução, e não uma instrução por linha: a importação "desde a posição"
    traz anos de pregões de cada ticker, e cada linha era uma ida e volta ao
    banco. Um lote não pode tocar a mesma linha duas vezes (o PostgreSQL
    recusa o ``ON CONFLICT DO UPDATE`` inteiro), por isso a entrada é
    reduzida antes a uma linha por (ticker, dia) — a de instante mais novo,
    e a última em caso de empate, exatamente a que sobraria gravando uma a
    uma com o filtro ``excluded.recorded_at >= recorded_at``. A ordem por
    chave faz duas gravações concorrentes (coletor e importação) travarem as
    linhas na mesma sequência, sem deadlock.

    Não faz ``commit``: quem inicia a operação de escrita é dono do limite
    transacional.
    """
    latest: dict[tuple[int, date], tuple[Decimal, datetime]] = {}
    for ticker_id, price, recorded_date, recorded_at in entries:
        key = (ticker_id, recorded_date)
        current = latest.get(key)
        if current is None or recorded_at >= current[1]:
            latest[key] = (price, recorded_at)
    rows = [
        {
            "ticker_id": ticker_id,
            "price": price,
            "recorded_date": recorded_date,
            "recorded_at": recorded_at,
        }
        for (ticker_id, recorded_date), (price, recorded_at) in sorted(latest.items())
    ]
    for start in range(0, len(rows), QUOTE_HISTORY_UPSERT_BATCH_SIZE):
        statement = insert(QuoteHistory).values(
            rows[start : start + QUOTE_HISTORY_UPSERT_BATCH_SIZE]
        )
        db.session.execute(
            statement.on_conflict_do_update(
                index_elements=[QuoteHistory.ticker_id, QuoteHistory.recorded_date],
                set_={
                    "price": statement.excluded.price,
                    "recorded_at": statement.excluded.recorded_at,
                },
                where=statement.excluded.recorded_at >= QuoteHistory.recorded_at,
            )
        )
