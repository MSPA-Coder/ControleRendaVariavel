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
    is_benchmark: bool


@dataclass(frozen=True, slots=True)
class BrokerInput:
    name: str
    acronym: str


@dataclass(frozen=True, slots=True)
class PortfolioCreateInput:
    name: str
    currency: str | None
    simulated: bool
    description: str | None


@dataclass(frozen=True, slots=True)
class PortfolioUpdateInput:
    """Igual a ``PortfolioCreateInput``, sem ``simulated``: a natureza
    real/simulada de uma carteira é fixada na criação (ver
    ``app.routes.tables.update_portfolio``) e não é lida do formulário de
    edição — bloqueado no servidor, não só escondido na UI."""

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
    simulated = form.get("simulated", "").strip().lower() in {"1", "true", "on", "yes"}
    description = _parse_portfolio_description(form)
    return PortfolioCreateInput(name, currency, simulated, description)


def parse_portfolio_update(form: Mapping[str, str]) -> PortfolioUpdateInput:
    name = _parse_portfolio_name(form)
    currency = _parse_portfolio_currency(form)
    description = _parse_portfolio_description(form)
    return PortfolioUpdateInput(name, currency, description)
