from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from flask import Flask

from app import db
from app.models import Broker, Market, Position, PositionKind, Side, Ticker
from app.routes.helpers import open_real_quantities_by_ticker

pytestmark = [pytest.mark.critical, pytest.mark.business_rule]


def test_open_real_quantities_by_ticker_applies_side_signal(app: Flask) -> None:
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
        db.session.add_all(
            [
                Position(
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
                ),
                Position(
                    broker_id=broker.id,
                    ticker_id=ticker.id,
                    quantity=Decimal("40"),
                    average_cost=Decimal("25"),
                    side=Side.SELL,
                    opened_on=date(2026, 1, 2),
                    quote_multiplier=Decimal("1"),
                    target_multiplier=Decimal("1.5"),
                    result_mode="L",
                    position_kind=PositionKind.REAL,
                ),
            ]
        )
        db.session.commit()

        assert open_real_quantities_by_ticker() == {ticker.id: Decimal("60")}
