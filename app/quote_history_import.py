from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.models import Market


class QuoteHistoryImportError(RuntimeError):
    """Expected failure while obtaining historical market data."""


@dataclass(frozen=True, slots=True)
class TickerImportTarget:
    id: int
    symbol: str
    market: Market


@dataclass(frozen=True, slots=True)
class DailyQuote:
    recorded_date: date
    price: Decimal
    recorded_at: datetime


def yahoo_symbol(target: TickerImportTarget) -> str:
    """Translate the internal ticker convention to Yahoo Finance symbols."""

    symbol = target.symbol.strip().upper()
    if target.market == Market.B3:
        if symbol in {"IBOV", "IBOVESPA", "^BVSP"}:
            return "^BVSP"
        return symbol if symbol.endswith(".SA") else f"{symbol}.SA"
    return symbol.replace(".", "-")


def _indicator_values(
    indicators: dict[str, Any], series_name: str, value_name: str, target: TickerImportTarget
) -> list[Any]:
    series = indicators.get(series_name, [])
    if not isinstance(series, list):
        raise QuoteHistoryImportError(f"Invalid Yahoo history received for {target.symbol}.")
    if not series:
        return []
    first_series = series[0]
    if not isinstance(first_series, dict):
        raise QuoteHistoryImportError(f"Invalid Yahoo history received for {target.symbol}.")
    values = first_series.get(value_name, [])
    if not isinstance(values, list):
        raise QuoteHistoryImportError(f"Invalid Yahoo history received for {target.symbol}.")
    return values


def fetch_yahoo_daily_quotes(
    target: TickerImportTarget,
    start_date: date,
    end_date: date,
    *,
    timeout_seconds: float = 10,
) -> list[DailyQuote]:
    """Fetch closing prices without holding a database transaction open."""

    period_start = int(datetime.combine(start_date, time.min, tzinfo=UTC).timestamp())
    period_end = int(
        datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC).timestamp()
    )
    params = urlencode(
        {
            "period1": period_start,
            "period2": period_end,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
    )
    symbol = yahoo_symbol(target)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?{params}"
    request = Request(url, headers={"User-Agent": "ControleRendaVariavel/1.0"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed HTTPS host
            payload: Any = json.load(response)
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuoteHistoryImportError(
            f"Yahoo Finance did not respond for {target.symbol}."
        ) from exc

    if not isinstance(payload, dict):
        raise QuoteHistoryImportError(f"Invalid Yahoo history received for {target.symbol}.")
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise QuoteHistoryImportError(f"Invalid Yahoo history received for {target.symbol}.")
    result = chart.get("result")
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        raise QuoteHistoryImportError(f"Yahoo Finance found no history for {target.symbol}.")
    series = result[0]
    timestamps = series.get("timestamp", [])
    indicators = series.get("indicators", {})
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        raise QuoteHistoryImportError(f"Invalid Yahoo history received for {target.symbol}.")
    closes = _indicator_values(indicators, "quote", "close", target)
    adjusted_closes = _indicator_values(indicators, "adjclose", "adjclose", target)

    quotes_by_date: dict[date, DailyQuote] = {}
    for index, timestamp in enumerate(timestamps):
        adjusted_close = adjusted_closes[index] if index < len(adjusted_closes) else None
        close = adjusted_close if adjusted_close is not None else (
            closes[index] if index < len(closes) else None
        )
        if close is None:
            continue
        try:
            recorded_at = datetime.fromtimestamp(int(timestamp), UTC)
            price = Decimal(str(close))
        except (TypeError, ValueError, InvalidOperation, OSError, OverflowError) as exc:
            raise QuoteHistoryImportError(
                f"Invalid Yahoo history received for {target.symbol}."
            ) from exc
        if not price.is_finite() or price < 0:
            raise QuoteHistoryImportError(f"Invalid Yahoo history received for {target.symbol}.")
        recorded_date = recorded_at.date()
        if start_date <= recorded_date <= end_date:
            quotes_by_date[recorded_date] = DailyQuote(recorded_date, price, recorded_at)
    return [quotes_by_date[recorded_date] for recorded_date in sorted(quotes_by_date)]
