"""Regressão de snapshots globais quando dois donos acompanham o mesmo ativo."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app import db
from app.collector.database import persist_readings
from app.collector.rtd import QuoteValue
from app.models import Broker, Market, Portfolio, Position, Quote, QuoteHistory, Side, Ticker, User
from app.positions.portfolio import effective_position_quote

pytestmark = pytest.mark.banco


def test_shared_ticker_keeps_latest_market_and_each_side_quote(app_com_banco):
    suffix = uuid4().hex[:8]
    newer = datetime.now(UTC).replace(microsecond=0)
    older = newer - timedelta(minutes=5)
    with app_com_banco.app_context():
        users = [User(username=f"collector-{suffix}-{i}", role="operador", is_active_user=True) for i in range(2)]
        for user in users:
            user.set_password("Synthetic-collector-only-2026!")
        broker = Broker(name=f"Collector {suffix}", acronym=suffix[:6])
        ticker = Ticker(symbol=f"C{suffix}", trading_name="Shared", market=Market.B3, rtd_market_code="B", currency="BRL")
        db.session.add_all([*users, broker, ticker])
        db.session.flush()
        portfolios = [Portfolio(owner_id=user.id, name=f"P-{suffix}-{i}", currency="BRL", simulated=False) for i, user in enumerate(users)]
        db.session.add_all(portfolios)
        db.session.flush()
        buy = Position(owner_id=users[0].id, broker_id=broker.id, ticker_id=ticker.id, portfolio_id=portfolios[0].id, quantity=1, average_cost=10, quote_multiplier=1, target_multiplier=1, side=Side.BUY, opened_on=date.today(), result_mode="L")
        sell = Position(owner_id=users[1].id, broker_id=broker.id, ticker_id=ticker.id, portfolio_id=portfolios[1].id, quantity=1, average_cost=10, quote_multiplier=1, target_multiplier=1, side=Side.SELL, opened_on=date.today(), result_mode="L")
        db.session.add_all([buy, sell])
        db.session.flush()
        persist_readings([
            QuoteValue(buy.id, Decimal("11"), Decimal("10"), "A", newer, Decimal("10.5")),
            QuoteValue(sell.id, Decimal("12"), Decimal("10"), "A", newer, Decimal("10.5")),
        ], [])
        persist_readings([QuoteValue(buy.id, Decimal("1"), Decimal("1"), "A", older)], [])
        db.session.flush()
        quote = db.session.get(Quote, ticker.id)
        assert quote is not None
        assert (quote.last_price, quote.buy_price, quote.sell_price, quote.observed_at) == (Decimal("10.5"), Decimal("11"), Decimal("12"), newer)
        assert effective_position_quote(buy)[0] == Decimal("11")
        assert effective_position_quote(sell)[0] == Decimal("12")
        history = db.session.query(QuoteHistory).filter_by(ticker_id=ticker.id).one()
        assert (history.price, history.recorded_at) == (Decimal("10.5"), newer)
        db.session.commit()
        position_ids = [buy.id, sell.id]
        portfolio_ids = [item.id for item in portfolios]
        user_ids = [user.id for user in users]
        db.session.execute(delete(Position).where(Position.id.in_(position_ids)))
        db.session.execute(delete(Portfolio).where(Portfolio.id.in_(portfolio_ids)))
        db.session.execute(delete(Ticker).where(Ticker.id == ticker.id))
        db.session.execute(delete(Broker).where(Broker.id == broker.id))
        db.session.execute(delete(User).where(User.id.in_(user_ids)))
        db.session.commit()
