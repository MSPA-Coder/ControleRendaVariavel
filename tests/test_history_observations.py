"""Observe real parsing/network outcomes; substitute only I/O and the clock."""

import io
import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.error import HTTPError, URLError

import pytest

from app.models import Market
from app.quotes import history_import as history

TARGET = history.TickerImportTarget(1, "PRIVATE_TICKER", Market.B3)


@pytest.fixture
def observations(monkeypatch, caplog):
    moments = iter((10.0, 10.125))
    monkeypatch.setattr(history, "perf_counter", lambda: next(moments))
    caplog.set_level(logging.INFO, logger=history.__name__)
    return caplog


@pytest.mark.parametrize("prices", [[], [12.34]])
def test_observation_keeps_parsed_result_and_timeout(monkeypatch, observations, prices):
    seen = []
    payload = {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": "America/Sao_Paulo"},
        "timestamp": [int(datetime(2026, 1, 2, 13, tzinfo=UTC).timestamp())] if prices else [],
        "indicators": {"quote": [{"close": prices}]},
    }]}}

    def respond(_request, *, timeout):
        seen.append(timeout)
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(history, "urlopen", respond)
    result = history.fetch_yahoo_daily_quotes(
        TARGET, date(2026, 1, 1), date(2026, 1, 3), timeout_seconds=2.5,
        now=datetime(2026, 1, 10, tzinfo=UTC),
    )
    assert [quote.price for quote in result] == [Decimal(str(price)) for price in prices]
    assert seen == [2.5]
    assert f"outcome={'ok' if prices else 'empty'} duration_ms=125.0 records={len(prices)}" in observations.text
    assert TARGET.symbol not in observations.text
    assert "12.34" not in observations.text


@pytest.mark.parametrize(("error", "outcome"), [
    (HTTPError("https://example.invalid/private", 429, "private message", None, None), "rate_limited"),
    (HTTPError("https://example.invalid/private", 503, "private message", None, None), "http_error"),
    (TimeoutError("private message"), "timeout"),
    (URLError(TimeoutError("private message")), "timeout"),
    (URLError("private message"), "connection_error"),
])
def test_failure_is_classified_without_retry_or_sensitive_data(monkeypatch, observations, error, outcome):
    calls = []

    def fail(_request, *, timeout):
        calls.append(timeout)
        raise error

    monkeypatch.setattr(history, "urlopen", fail)
    with pytest.raises(history.QuoteHistoryImportError) as caught:
        history.fetch_yahoo_daily_quotes(TARGET, date(2026, 1, 1), date(2026, 1, 3))
    assert caught.value.__cause__ is error
    assert calls == [10]
    assert f"outcome={outcome} duration_ms=125.0 records=0" in observations.text
    assert "private" not in observations.text
    assert TARGET.symbol not in observations.text


def test_invalid_payload_is_still_rejected(monkeypatch, observations):
    monkeypatch.setattr(history, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"{}"))
    with pytest.raises(history.QuoteHistoryImportError):
        history.fetch_yahoo_daily_quotes(TARGET, date(2026, 1, 1), date(2026, 1, 3))
    assert "outcome=invalid_data duration_ms=125.0 records=0" in observations.text
