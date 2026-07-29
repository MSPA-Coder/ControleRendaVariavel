from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.models import Market


@dataclass(frozen=True, slots=True)
class TickerInput:
    symbol: str
    trading_name: str
    market: Market
    rtd_market_code: str
    currency: str


@dataclass(frozen=True, slots=True)
class BrokerInput:
    name: str
    acronym: str


def parse_broker(form: Mapping[str, str]) -> BrokerInput:
    name = " ".join(form.get("name", "").split())
    if not name or len(name) > 40:
        raise ValueError("Informe uma corretora com até 40 caracteres.")
    acronym = " ".join(form.get("acronym", "").split()).upper()
    if not acronym or len(acronym) > 40:
        raise ValueError("Informe uma sigla de corretora válida.")
    return BrokerInput(name, acronym)


def parse_ticker(form: Mapping[str, str]) -> TickerInput:
    symbol = form.get("symbol", "").strip().upper()
    trading_name = " ".join(form.get("trading_name", "").split())
    if not symbol or len(symbol) > 24:
        raise ValueError("Informe um ticker com até 24 caracteres.")
    try:
        market = Market(form.get("market", ""))
    except ValueError as exc:
        raise ValueError("Mercado inválido.") from exc
    rtd_market_code = form.get("rtd_market_code", "").strip().upper()
    if rtd_market_code not in {"B", "Y", "N"}:
        raise ValueError("Código RTD inválido.")
    currency = form.get("currency", "").strip().upper()
    if currency not in {"BRL", "USD"}:
        raise ValueError("Moeda inválida.")
    if not trading_name or len(trading_name) > 80:
        raise ValueError("Informe um nome de pregão válido.")
    return TickerInput(symbol, trading_name, market, rtd_market_code, currency)
