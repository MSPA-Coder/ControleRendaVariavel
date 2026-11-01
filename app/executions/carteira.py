"""Carteira do exterior: a posição do CRV contra a carteira do extrato da corretora.

Só leitura. O CB guarda a carteira de ações que cada extrato mensal da corretora
imprime (``statement_holdings`` do contrato v4; hoje só o da Avenue em dólar) e
o CRV compara com a quantidade que ele tem. É a única conferência independente
da quantidade no exterior: a do myProfit (``conciliacao.build_exterior``) chega
com dias de atraso e é ela mesma uma cópia.

O extrato mostra a posição **liquidada**. O negócio do último pregão do mês
liquida no mês seguinte (D+1 nos EUA), então o CRV é lido no último pregão cuja
liquidação cabe no extrato (``settled_cutoff``). Feriado dos EUA não é
considerado: num pregão vizinho a um feriado a conferência pode acusar uma
diferença que o extrato seguinte desfaz.

``compare_holdings`` e ``settled_cutoff`` são puras; ``holdings_rows`` lê a
linha do tempo de posições do banco.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app import db
from app.models import Broker, Ticker
from app.patrimonio.queries import position_timeline
from app.positions.holdings_history import QuantityTimeline


def symbol_key(instrument: str) -> str:
    """O ativo sem pontuação: o extrato da Avenue imprime BRK.B e BRKB."""
    return re.sub(r"[^A-Z0-9]", "", instrument.upper())


@dataclass(frozen=True, slots=True)
class HoldingCheck:
    """Um ativo numa data de extrato: a quantidade de cada lado."""

    broker: str
    statement_date: date
    #: O pregão em que o CRV foi lido (``settled_cutoff`` da data do extrato).
    position_date: date
    instrument: str
    statement_quantity: Decimal
    crv_quantity: Decimal

    @property
    def matches(self) -> bool:
        return self.statement_quantity == self.crv_quantity


def _previous_business_day(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def settled_cutoff(statement_date: date) -> date:
    """Último pregão cuja liquidação (D+1 útil) cai até a data do extrato.

    Extrato de quarta 30/09 mostra o que foi negociado até terça 29/09; extrato de
    sábado 31/01 mostra o negociado até quinta 29/01 (o de sexta liquida na segunda).
    """
    last_business_day = statement_date
    while last_business_day.weekday() >= 5:
        last_business_day -= timedelta(days=1)
    return _previous_business_day(last_business_day)


def compare_holdings(
    broker: str,
    statement_date: date,
    position_date: date,
    statement: Mapping[str, Decimal],
    crv: Mapping[str, Decimal],
) -> list[HoldingCheck]:
    """Um ``HoldingCheck`` por ativo de qualquer dos lados, em ordem de ativo.

    O ativo é casado sem pontuação (``symbol_key``): o extrato imprimiu BRKB em
    janeiro e BRK.B depois. Quantidade zero do CRV não conta como posição.
    """
    by_key: dict[str, list] = {}
    for instrument, quantity in statement.items():
        entry = by_key.setdefault(symbol_key(instrument), [instrument, Decimal("0"), Decimal("0")])
        entry[1] += quantity
    for instrument, quantity in crv.items():
        if quantity == 0:
            continue
        entry = by_key.setdefault(symbol_key(instrument), [instrument, Decimal("0"), Decimal("0")])
        entry[0] = instrument  # o símbolo do CRV, que é o do cadastro
        entry[2] += quantity
    return [
        HoldingCheck(broker, statement_date, position_date, instrument, statement_quantity, crv_quantity)
        for _, (instrument, statement_quantity, crv_quantity) in sorted(by_key.items())
    ]


def holdings_rows(
    owner_id: int,
    holdings: Mapping[tuple[str, str], Mapping[date, Mapping[str, Decimal]]],
    institution_to_broker: Mapping[str, str],
    *,
    broker: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[HoldingCheck]:
    """Compara cada carteira de extrato com a posição do CRV na corretora correspondente.

    ``holdings`` é o ``CbSettlements.holdings``; instituição sem corretora no CRV
    fica de fora. Só entram as ações na moeda da conta do extrato.
    """
    wanted: list[tuple[str, str, date, Mapping[str, Decimal]]] = []
    for (institution, currency), statements in holdings.items():
        broker_name = institution_to_broker.get(institution)
        if broker_name is None or (broker and broker_name != broker):
            continue
        for statement_date, positions in statements.items():
            if (start and statement_date < start) or (end and statement_date > end):
                continue
            wanted.append((broker_name, currency, statement_date, positions))
    if not wanted:
        return []

    broker_ids = dict(
        db.session.execute(
            select(Broker.name, Broker.id).where(Broker.name.in_({item[0] for item in wanted}))
        ).all()
    )
    tickers = {ticker.id: ticker for ticker in db.session.scalars(select(Ticker))}
    events = position_timeline(owner_id, include_options=False)
    timelines: dict[str, QuantityTimeline] = {}
    rows: list[HoldingCheck] = []
    for broker_name, currency, statement_date, positions in sorted(wanted, key=lambda item: (item[2], item[0])):
        broker_id = broker_ids.get(broker_name)
        if broker_name not in timelines:
            timelines[broker_name] = QuantityTimeline([event for event in events if event.broker_id == broker_id])
        timeline = timelines[broker_name]
        position_date = settled_cutoff(statement_date)
        crv: dict[str, Decimal] = {}
        for ticker_id in timeline.ticker_ids:
            ticker = tickers[ticker_id]
            if ticker.currency == currency:
                crv[ticker.symbol] = timeline.quantity_at(ticker_id, position_date)
        rows.extend(compare_holdings(broker_name, statement_date, position_date, positions, crv))
    return rows


def pending(rows: Iterable[HoldingCheck]) -> list[HoldingCheck]:
    return [row for row in rows if not row.matches]
