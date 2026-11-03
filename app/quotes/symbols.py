"""Código vigente e fator de cotação: o que liga a bolsa ao cadastro.

Duas coisas mudam quando a bolsa troca o código de um ativo e passa a cotá-lo
por lote (RAIZ4 → RAIZ55, R$ por 100 ações, em 03/11/2026), e nenhuma delas
deve chegar ao resto do sistema:

- **o preço** chega por lote; ``per_unit`` o devolve por unidade, na entrada
  (coletor e Yahoo), para que quantidade × cotação continue valendo;
- **o código antigo** continua chegando de fontes que olham o passado -- o CB,
  o myProfit, os extratos de corretora; ``former_symbol_map`` diz qual é o
  código vigente de cada um.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select

from app import db
from app.accounts.auditoria import registrar
from app.models import QuoteHistory, Ticker, TickerFormerSymbol
from app.quotes.reference_data import _SYMBOL_PATTERN, MAX_QUOTE_FACTOR


def per_unit(price: Decimal, quote_factor: int) -> Decimal:
    """O preço publicado (por lote de ``quote_factor``) levado à unidade."""
    return price if quote_factor == 1 else price / quote_factor


def former_symbol_map() -> dict[str, str]:
    """Código anterior → código vigente, ambos em maiúsculas."""
    return {
        former.upper(): current.upper()
        for former, current in db.session.execute(
            select(TickerFormerSymbol.symbol, Ticker.symbol).join(
                Ticker, Ticker.id == TickerFormerSymbol.ticker_id
            )
        )
    }


def current_symbol(symbol: str, former: Mapping[str, str]) -> str:
    """O código vigente de ``symbol``; o próprio, se ele nunca foi trocado."""
    return former.get(symbol.upper(), symbol)


def first_day_of_current_symbol() -> dict[int, date]:
    """Para cada ticker que trocou de código, o primeiro dia do código atual.

    É o dia seguinte ao último pregão do código anterior mais recente. A
    fonte externa (Yahoo) não tem o código novo antes dele, e as datas
    anteriores já têm a série do código antigo, que não deve ser regravada.
    """
    return {
        ticker_id: last_day + timedelta(days=1)
        for ticker_id, last_day in db.session.execute(
            select(TickerFormerSymbol.ticker_id, func.max(TickerFormerSymbol.last_trading_day)).group_by(
                TickerFormerSymbol.ticker_id
            )
        )
    }


@dataclass(frozen=True, slots=True)
class SymbolChange:
    """O que ``flask trocar-codigo`` faria, para o ensaio mostrar e a gravação usar."""

    ticker_id: int
    old_symbol: str
    new_symbol: str
    last_trading_day: date
    old_factor: int
    new_factor: int
    stale_history: tuple[tuple[date, Decimal], ...]
    """Cotações gravadas depois do último pregão do código antigo: o coletor
    continuou lendo o código morto, que repete o último preço. Saem para o
    código novo preencher esses dias."""


def plan_symbol_change(
    old_symbol: str, new_symbol: str, *, last_trading_day: date, quote_factor: int
) -> SymbolChange:
    """Valida a troca de código e diz o que ela faria; não grava nada.

    Levanta ``ValueError`` com a razão quando a troca não pode ser feita.
    """
    old_symbol, new_symbol = old_symbol.strip().upper(), new_symbol.strip().upper()
    ticker = db.session.scalar(select(Ticker).where(func.upper(Ticker.symbol) == old_symbol))
    if ticker is None:
        raise ValueError(f"{old_symbol} não está cadastrado.")
    if not ticker.is_active:
        raise ValueError(f"{old_symbol} está arquivado: reative-o antes.")
    if not new_symbol or len(new_symbol) > 24 or not _SYMBOL_PATTERN.fullmatch(new_symbol):
        raise ValueError(f"{new_symbol or 'O código novo'} não é um código válido.")
    if new_symbol == old_symbol:
        raise ValueError("O código novo é igual ao atual.")
    if db.session.scalar(select(Ticker.id).where(func.upper(Ticker.symbol) == new_symbol)) is not None:
        raise ValueError(f"{new_symbol} já está cadastrado.")
    if new_symbol in former_symbol_map():
        raise ValueError(f"{new_symbol} já foi o código de outro cadastro.")
    if not 1 <= quote_factor <= MAX_QUOTE_FACTOR:
        raise ValueError(f"O fator de cotação vai de 1 a {MAX_QUOTE_FACTOR}.")
    stale = db.session.execute(
        select(QuoteHistory.recorded_date, QuoteHistory.price)
        .where(QuoteHistory.ticker_id == ticker.id, QuoteHistory.recorded_date > last_trading_day)
        .order_by(QuoteHistory.recorded_date)
    ).all()
    return SymbolChange(
        ticker_id=ticker.id,
        old_symbol=ticker.symbol,
        new_symbol=new_symbol,
        last_trading_day=last_trading_day,
        old_factor=ticker.quote_factor,
        new_factor=quote_factor,
        stale_history=tuple((row.recorded_date, row.price) for row in stale),
    )


def apply_symbol_change(change: SymbolChange) -> None:
    """Grava a troca planejada (sem commit): o cadastro é o mesmo, com o código
    e o fator novos, e o código antigo fica guardado como anterior."""
    ticker = db.session.get(Ticker, change.ticker_id)
    assert ticker is not None and ticker.symbol == change.old_symbol
    db.session.add(
        TickerFormerSymbol(
            ticker_id=ticker.id, symbol=change.old_symbol, last_trading_day=change.last_trading_day
        )
    )
    ticker.symbol = change.new_symbol
    ticker.quote_factor = change.new_factor
    db.session.execute(
        delete(QuoteHistory).where(
            QuoteHistory.ticker_id == ticker.id, QuoteHistory.recorded_date > change.last_trading_day
        )
    )
    registrar(
        "ticker",
        "atualizar",
        entidade_id=ticker.id,
        detalhes={
            "troca_de_codigo": f"{change.old_symbol} -> {change.new_symbol}",
            "ultimo_pregao": change.last_trading_day.isoformat(),
            "fator_de_cotacao": f"{change.old_factor} -> {change.new_factor}",
            "cotacoes_removidas": len(change.stale_history),
        },
    )
