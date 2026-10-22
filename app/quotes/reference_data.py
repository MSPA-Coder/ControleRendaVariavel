from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.models import Market

#: Letras, números e os separadores que os provedores usam (BRK.B,
#: BRK-B, USDBRL=X, ^BVSP). Espaço, ponto e vírgula e aspas ficam
#: de fora: o código segue para a coleta de cotações e para URLs.
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]*$")

#: Moeda em que cada mercado cota. Um ticker da B3 em dólar, ou da NYSE em
#: reais, nunca é o que a pessoa quis cadastrar.
_MARKET_CURRENCY = {Market.B3: "BRL", Market.NYSE: "USD", Market.NASDAQ: "USD"}


@dataclass(frozen=True, slots=True)
class TickerInput:
    symbol: str
    trading_name: str
    market: Market
    rtd_market_code: str
    currency: str
    is_benchmark: bool


@dataclass(frozen=True, slots=True)
class BrokerInput:
    name: str
    acronym: str


@dataclass(frozen=True, slots=True)
class PortfolioCreateInput:
    name: str
    currency: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class PortfolioUpdateInput:
    """Os campos que o formulário de edição altera: os mesmos da criação."""

    name: str
    currency: str | None
    description: str | None


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
    if not _SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError(
            "O ticker aceita só letras, números e os símbolos . - = ^, sem espaços."
        )
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
    is_benchmark = form.get("is_benchmark", "").strip().lower() in {"1", "true", "on", "yes"}
    esperada = _MARKET_CURRENCY.get(market)
    if not is_benchmark and esperada is not None and currency != esperada:
        raise ValueError(f"Um ticker de {market.value} é cotado em {esperada}, não em {currency}.")
    return TickerInput(symbol, trading_name, market, rtd_market_code, currency, is_benchmark)


def _parse_portfolio_name(form: Mapping[str, str]) -> str:
    name = " ".join(form.get("name", "").split())
    if not name or len(name) > 80:
        raise ValueError("Informe um nome de carteira com até 80 caracteres.")
    return name


def _parse_portfolio_currency(form: Mapping[str, str]) -> str | None:
    currency = form.get("currency", "").strip().upper()
    if not currency:
        return None
    if currency not in {"BRL", "USD"}:
        raise ValueError("Moeda inválida.")
    return currency


def _parse_portfolio_description(form: Mapping[str, str]) -> str | None:
    description = " ".join(form.get("description", "").split())
    if len(description) > 500:
        raise ValueError("A descrição pode ter até 500 caracteres.")
    return description or None


def parse_portfolio_create(form: Mapping[str, str]) -> PortfolioCreateInput:
    name = _parse_portfolio_name(form)
    currency = _parse_portfolio_currency(form)
    description = _parse_portfolio_description(form)
    return PortfolioCreateInput(name, currency, description)


def parse_portfolio_update(form: Mapping[str, str]) -> PortfolioUpdateInput:
    name = _parse_portfolio_name(form)
    currency = _parse_portfolio_currency(form)
    description = _parse_portfolio_description(form)
    return PortfolioUpdateInput(name, currency, description)
