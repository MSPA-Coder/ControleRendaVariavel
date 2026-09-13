"""Schema recém-migrado: bootstrap seguro e integridade entre proprietários."""

from datetime import date

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Broker, Market, Portfolio, Position, Side, Ticker, User
from tests.test_financial_ownership_migration import legacy_app as legacy_app

pytestmark = pytest.mark.banco


@pytest.mark.parametrize('remove_defaults', [False, True])
def test_empty_bootstrap_does_not_invent_owner(legacy_app, remove_defaults):
    with legacy_app.app_context():
        if remove_defaults:
            db.session.execute(text('DELETE FROM portfolios'))
            db.session.commit()
        upgrade()
        assert db.session.scalar(text('SELECT count(*) FROM users')) == 0
        assert db.session.scalar(text('SELECT count(*) FROM portfolios')) == 0
        assert db.session.scalar(text('SELECT version_num FROM alembic_version')) == '20260912_0016'


@pytest.mark.parametrize('customization', ["description='preservar configuração'", 'is_active=false'])
def test_customized_baseline_is_not_discarded_without_mspa(legacy_app, customization):
    with legacy_app.app_context():
        db.session.execute(text(f"UPDATE portfolios SET {customization} WHERE name='BRL'"))
        before = db.session.execute(text('SELECT * FROM portfolios ORDER BY id')).all()
        db.session.commit()
        with pytest.raises(SystemExit):
            upgrade()
        assert db.session.execute(text('SELECT * FROM portfolios ORDER BY id')).all() == before
        assert db.session.scalar(text('SELECT version_num FROM alembic_version')) == '20260904_0015'


def test_newly_migrated_schema_matches_models(legacy_app):
    with legacy_app.app_context():
        upgrade()
        with db.engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={'compare_type': True})
            differences = compare_metadata(context, db.metadata)
        assert differences == []


def test_database_rejects_financial_links_between_owners(legacy_app):
    with legacy_app.app_context():
        upgrade()
        users = [User(username=name, password_hash='test-only-invalid-hash', role='operador') for name in ('a', 'b')]
        broker = Broker(name='Corretora global', acronym='TEST')
        ticker = Ticker(symbol='TEST3', trading_name='Ativo', market=Market.B3, currency='BRL', rtd_market_code='B')
        db.session.add_all([*users, broker, ticker])
        db.session.flush()
        portfolios = [Portfolio(name='Mesma carteira', owner_id=user.id, currency='BRL', simulated=False) for user in users]
        db.session.add_all(portfolios)
        db.session.flush()
        with pytest.raises(IntegrityError) as error, db.session.begin_nested():
            db.session.add(Position(owner_id=users[0].id, portfolio_id=portfolios[1].id,
                                    broker_id=broker.id, ticker_id=ticker.id, quantity=1,
                                    average_cost=10, side=Side.BUY, opened_on=date.today(),
                                    quote_multiplier=1, target_multiplier=1.5, result_mode='L'))
            db.session.flush()
        assert 'fk_positions_portfolio_owner' in str(error.value)
        db.session.rollback()
