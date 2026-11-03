"""Schema recém-migrado: igual aos modelos e íntegro entre proprietários."""

from datetime import date

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Broker, Market, Portfolio, Position, Side, Ticker, User

pytestmark = pytest.mark.banco


def test_newly_migrated_schema_matches_models(app_com_banco):
    with app_com_banco.app_context(), db.engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, db.metadata)
    assert differences == []


def test_database_rejects_financial_links_between_owners(sessao):
    users = [User(username=name, password_hash="test-only-invalid-hash", role="operador") for name in ("a", "b")]
    broker = Broker(name="Corretora global", acronym="TEST")
    ticker = Ticker(symbol="TEST3", trading_name="Ativo", market=Market.B3, currency="BRL", rtd_market_code="B")
    sessao.add_all([*users, broker, ticker])
    sessao.flush()
    portfolios = [Portfolio(name="Mesma carteira", owner_id=user.id, currency="BRL") for user in users]
    sessao.add_all(portfolios)
    sessao.flush()
    with pytest.raises(IntegrityError) as error, sessao.begin_nested():
        sessao.add(Position(owner_id=users[0].id, portfolio_id=portfolios[1].id,
                            broker_id=broker.id, ticker_id=ticker.id, quantity=1,
                            average_cost=10, side=Side.BUY, opened_on=date.today(),
                            target_multiplier=1.5))
        sessao.flush()
    assert "fk_positions_portfolio_owner" in str(error.value)
