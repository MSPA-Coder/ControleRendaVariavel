"""Servidor exclusivo do piloto de navegador; nunca usa dados operacionais."""

import os
from datetime import UTC, date, datetime
from decimal import Decimal

from flask_migrate import upgrade

from app import create_app, db
from app.models import (
    Broker,
    Market,
    Portfolio,
    Position,
    Quote,
    Side,
    Ticker,
    User,
    UserPreference,
    UserTickerEntitlement,
)
from app.positions.closure import create_or_merge_position


def main():
    if os.environ.get("BROWSER_TEST_SERVER") != "1":
        raise RuntimeError("Servidor restrito ao Compose de testes de navegador.")
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "browser-tests-only-not-an-operational-secret",
        "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://browser:browser@browser-db:5432/browser_tests",
    })
    with app.app_context():
        upgrade()
        if db.session.query(User).count():
            raise RuntimeError("Banco do navegador deve nascer vazio; recrie a pilha de teste.")
        broker = Broker(name="Corretora browser", acronym="BROWSER")
        tickers = [Ticker(symbol=symbol, trading_name=symbol, market=Market.B3, currency="BRL", rtd_market_code="B")
                   for symbol in ("BROWSE3", "RETRY3", "FLOW3")]
        db.session.add_all([broker, *tickers])
        db.session.flush()
        for region in ("br", "us", "flow"):
            user = User(username=f"browser-{region}", role="operador", must_change_password=False)
            user.set_password("Synthetic-browser-only-2026!")
            db.session.add(user)
            db.session.flush()
            portfolio = Portfolio(owner_id=user.id, name="Carteira browser", currency="BRL", simulated=False)
            db.session.add_all([portfolio, UserPreference(user_id=user.id, regional_format="us" if region == "us" else "br")])
            db.session.flush()
            if region == "flow":
                create_or_merge_position(Position(
                    owner_id=user.id, broker_id=broker.id, ticker_id=tickers[2].id,
                    portfolio_id=portfolio.id, quantity=Decimal("20"), average_cost=Decimal("10"),
                    side=Side.BUY, opened_on=date(2026, 6, 10), target_multiplier=Decimal("1.5"),
                ))
                db.session.add(UserTickerEntitlement(user_id=user.id, ticker_id=tickers[2].id, first_held_on=date(2026, 6, 10)))
        for ticker in tickers:
            db.session.add(Quote(ticker_id=ticker.id, last_price=Decimal("22.50"), previous_close=Decimal("22"), observed_at=datetime.now(UTC)))
        db.session.commit()
    app.run(host="0.0.0.0", port=5003, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
