from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.models import Market


@dataclass(frozen=True, slots=True)
class TickerInput:
    symbol: str
    market: Market
    rtd_market_code: str
    currency: str


def parse_broker_name(form: Mapping[str, str]) -> str:
    name = " ".join(form.get("name", "").split())
    if not name or len(name) > 40:
        raise ValueError("Informe uma corretora com até 40 caracteres.")
    return name


def parse_ticker(form: Mapping[str, str]) -> TickerInput:
    symbol = form.get("symbol", "").strip().upper()
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
    return TickerInput(symbol, market, rtd_market_code, currency)
