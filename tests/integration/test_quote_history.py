from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from flask import Flask

from app import db
from app.models import Broker, Market, Position, PositionKind, QuoteHistory, Side, Ticker
from app.rtd import QuoteValue

pytestmark = [pytest.mark.critical]


class _FakeProvider:
    def __init__(self, price: Decimal) -> None:
        self.price = price

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def fetch(self, instruments: list) -> list[QuoteValue]:  # type: ignore[type-arg]
        now = datetime.now(UTC)
        return [
            QuoteValue(item.position_id, self.price, self.price, "OK", now) for item in instruments
        ]


def _seed_position(app: Flask) -> int:
    with app.app_context():
        broker = Broker(name="XP", acronym="XP")
        ticker = Ticker(
            symbol="PETR4",
            trading_name="Petrobras",
            market=Market.B3,
            rtd_market_code="B",
            currency="BRL",
        )
        db.session.add_all([broker, ticker])
        db.session.commit()
        position = Position(
            broker_id=broker.id,
            ticker_id=ticker.id,
            quantity=Decimal("100"),
            average_cost=Decimal("20"),
            side=Side.BUY,
            opened_on=date(2026, 1, 1),
            quote_multiplier=Decimal("1"),
            target_multiplier=Decimal("1.5"),
            result_mode="L",
            position_kind=PositionKind.REAL,
        )
        db.session.add(position)
        db.session.commit()
        return ticker.id


def test_poll_rtd_records_one_quote_history_row_per_ticker_per_day(
    app: Flask, monkeypatch
) -> None:
    ticker_id = _seed_position(app)
    monkeypatch.setattr(
        "app.cli.ExcelRtdQuoteProvider", lambda **kwargs: _FakeProvider(Decimal("25"))
    )

    runner = app.test_cli_runner()
    result = runner.invoke(args=["poll-rtd"])
    assert result.exit_code == 0, result.output

    with app.app_context():
        rows = db.session.query(QuoteHistory).filter_by(ticker_id=ticker_id).all()
        assert len(rows) == 1
        assert rows[0].price == Decimal("25.00000000")

    # Segunda chamada no MESMO dia, preço diferente: deve atualizar a
    # mesma linha (upsert por ticker+dia), não criar uma segunda.
    monkeypatch.setattr(
        "app.cli.ExcelRtdQuoteProvider", lambda **kwargs: _FakeProvider(Decimal("27"))
    )
    result = runner.invoke(args=["poll-rtd"])
    assert result.exit_code == 0, result.output

    with app.app_context():
        rows = db.session.query(QuoteHistory).filter_by(ticker_id=ticker_id).all()
        assert len(rows) == 1
        assert rows[0].price == Decimal("27.00000000")
