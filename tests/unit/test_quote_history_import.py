from __future__ import annotations

import json
from datetime import UTC, date, datetime
from io import BytesIO

import pytest

from app.models import Market
from app.quote_history_import import (
    QuoteHistoryImportError,
    TickerImportTarget,
    fetch_yahoo_daily_quotes,
)

pytestmark = [pytest.mark.critical]


class _Response(BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def test_fetch_yahoo_daily_quotes_prefers_adjusted_close(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [int(datetime(2026, 1, 2, tzinfo=UTC).timestamp())],
                    "indicators": {
                        "quote": [{"close": [10.0]}],
                        "adjclose": [{"adjclose": [8.5]}],
                    },
                }
            ]
        }
    }

    monkeypatch.setattr(
        "app.quote_history_import.urlopen",
        lambda request, timeout=10: _Response(json.dumps(payload).encode()),
    )

    quotes = fetch_yahoo_daily_quotes(
        TickerImportTarget(1, "PETR4", Market.B3),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
    )

    assert len(quotes) == 1
    assert str(quotes[0].price) == "8.5"


def test_fetch_yahoo_daily_quotes_falls_back_to_close_when_adjclose_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [int(datetime(2026, 1, 2, tzinfo=UTC).timestamp())],
                    "indicators": {
                        "quote": [{"close": [10.0]}],
                    },
                }
            ]
        }
    }

    monkeypatch.setattr(
        "app.quote_history_import.urlopen",
        lambda request, timeout=10: _Response(json.dumps(payload).encode()),
    )

    quotes = fetch_yahoo_daily_quotes(
        TickerImportTarget(1, "PETR4", Market.B3),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
    )

    assert len(quotes) == 1
    assert str(quotes[0].price) == "10.0"


def test_fetch_yahoo_daily_quotes_rejects_malformed_indicator_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [int(datetime(2026, 1, 2, tzinfo=UTC).timestamp())],
                    "indicators": {"quote": "not-a-list"},
                }
            ]
        }
    }
    monkeypatch.setattr(
        "app.quote_history_import.urlopen",
        lambda request, timeout=10: _Response(json.dumps(payload).encode()),
    )

    with pytest.raises(QuoteHistoryImportError):
        fetch_yahoo_daily_quotes(
            TickerImportTarget(1, "PETR4", Market.B3),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 3),
        )


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        json.dumps(
            {
                "chart": {
                    "result": [
                        {
                            "timestamp": [10**100],
                            "indicators": {"quote": [{"close": [10]}]},
                        }
                    ]
                }
            }
        ).encode(),
    ],
)
def test_fetch_yahoo_daily_quotes_wraps_decode_and_timestamp_errors(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    monkeypatch.setattr(
        "app.quote_history_import.urlopen",
        lambda request, timeout=10: _Response(payload),
    )

    with pytest.raises(QuoteHistoryImportError):
        fetch_yahoo_daily_quotes(
            TickerImportTarget(1, "PETR4", Market.B3),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 3),
        )
