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
from tests.test_financial_ownership_migration import _legacy_rows
from tests.test_financial_ownership_migration import legacy_app as legacy_app

pytestmark = pytest.mark.banco

_PRE_HYGIENE_REVISION = "20260912_0016"
_HYGIENE_REVISION = "20260913_0017"
_COLLECTOR_CLEANUP_REVISION = "20260913_0018"
_LEGACY_POSITION_COLUMNS = ("broker", "ticker", "market", "rtd_market_code", "currency")
_LEGACY_COLLECTOR_COLUMNS = ("collector_mode", "collector_destination")
_TIMESTAMP_COLUMNS = (
    ("users", "created_at"),
    ("users", "updated_at"),
    ("portfolios", "created_at"),
    ("portfolios", "updated_at"),
    ("positions", "created_at"),
    ("positions", "updated_at"),
    ("position_movements", "created_at"),
    ("option_positions", "created_at"),
    ("option_positions", "updated_at"),
    ("option_position_movements", "created_at"),
    ("transactions", "created_at"),
    ("dividends", "created_at"),
)


def _create_schema_drift(app, *, populate_legacy_column: bool) -> None:
    """Reproduz o banco implantado antes da revisão de higienização."""
    with app.app_context(), db.engine.begin() as connection:
        connection.execute(text("ALTER TABLE positions ADD COLUMN broker varchar(40)"))
        connection.execute(text("ALTER TABLE positions ADD COLUMN ticker varchar(24)"))
        connection.execute(text("ALTER TABLE positions ADD COLUMN market market"))
        connection.execute(text("ALTER TABLE positions ADD COLUMN rtd_market_code varchar(1)"))
        connection.execute(text("ALTER TABLE positions ADD COLUMN currency varchar(3)"))
        connection.execute(text("CREATE INDEX ix_positions_ticker ON positions(ticker)"))
        for table_name, column_name in _TIMESTAMP_COLUMNS:
            connection.execute(text(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} DROP NOT NULL"))
            connection.execute(text(f"UPDATE {table_name} SET {column_name}=NULL"))
        if populate_legacy_column:
            connection.execute(text("UPDATE positions SET broker='dado legado' WHERE id=1"))


def _upgrade_to_pre_hygiene(app) -> None:
    _legacy_rows(app)
    with app.app_context():
        upgrade(revision=_PRE_HYGIENE_REVISION)
        assert db.session.scalar(text("SELECT version_num FROM alembic_version")) == _PRE_HYGIENE_REVISION


@pytest.mark.parametrize('remove_defaults', [False, True])
def test_empty_bootstrap_does_not_invent_owner(legacy_app, remove_defaults):
    with legacy_app.app_context():
        if remove_defaults:
            db.session.execute(text('DELETE FROM portfolios'))
            db.session.commit()
        upgrade()
        assert db.session.scalar(text('SELECT count(*) FROM users')) == 0
        assert db.session.scalar(text('SELECT count(*) FROM portfolios')) == 0
        assert db.session.scalar(text('SELECT version_num FROM alembic_version')) == _COLLECTOR_CLEANUP_REVISION


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


def test_schema_hygiene_migrates_drifted_legacy_schema_without_differences(legacy_app):
    _upgrade_to_pre_hygiene(legacy_app)
    _create_schema_drift(legacy_app, populate_legacy_column=False)

    with legacy_app.app_context():
        upgrade()
        assert db.session.scalar(text("SELECT version_num FROM alembic_version")) == _COLLECTOR_CLEANUP_REVISION
        remaining_columns = db.session.scalars(
            text(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema=current_schema() AND table_name='positions'
                """
            )
        ).all()
        assert not set(_LEGACY_POSITION_COLUMNS).intersection(remaining_columns)
        assert db.session.scalar(text("SELECT to_regclass('ix_positions_ticker')")) is None
        null_timestamps = db.session.execute(
            text(
                " UNION ALL ".join(
                    f"SELECT '{table_name}.{column_name}' "
                    f"WHERE EXISTS (SELECT 1 FROM {table_name} WHERE {column_name} IS NULL)"
                    for table_name, column_name in _TIMESTAMP_COLUMNS
                )
            )
        ).all()
        assert null_timestamps == []
        not_null = set(
            db.session.execute(
                text(
                    """
                    SELECT table_name, column_name FROM information_schema.columns
                    WHERE table_schema=current_schema() AND is_nullable='NO'
                    """
                )
            ).all()
        )
        assert set(_TIMESTAMP_COLUMNS).issubset(not_null)
        with db.engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={'compare_type': True})
            differences = compare_metadata(context, db.metadata)
        assert differences == []


def test_schema_hygiene_refuses_to_drop_unmapped_legacy_values(legacy_app):
    _upgrade_to_pre_hygiene(legacy_app)
    _create_schema_drift(legacy_app, populate_legacy_column=True)

    with legacy_app.app_context(), pytest.raises(SystemExit):
        upgrade()

    with legacy_app.app_context():
        assert db.session.scalar(text("SELECT version_num FROM alembic_version")) == _PRE_HYGIENE_REVISION
        assert db.session.scalar(text("SELECT broker FROM positions WHERE id=1")) == "dado legado"


def test_newly_migrated_schema_matches_models(legacy_app):
    with legacy_app.app_context():
        upgrade()
        with db.engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            differences = compare_metadata(context, db.metadata)
        assert differences == []


def test_collector_legacy_configuration_is_removed_after_agent_upgrade(legacy_app):
    with legacy_app.app_context():
        upgrade(revision=_HYGIENE_REVISION)
        columns_before = set(
            db.session.scalars(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema=current_schema() AND table_name='app_settings'
                    """
                )
            ).all()
        )
        assert set(_LEGACY_COLLECTOR_COLUMNS).issubset(columns_before)

        upgrade()
        columns_after = set(
            db.session.scalars(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema=current_schema() AND table_name='app_settings'
                    """
                )
            ).all()
        )
        assert not set(_LEGACY_COLLECTOR_COLUMNS).intersection(columns_after)
        assert db.session.scalar(text("SELECT to_regtype('collector_mode')")) is None
        assert db.session.scalar(text("SELECT to_regtype('collector_destination')")) is None
        assert db.session.scalar(text("SELECT version_num FROM alembic_version")) == _COLLECTOR_CLEANUP_REVISION

        legacy_app.config["COLLECTOR_AGENT_TOKEN"] = "a" * 32
        response = legacy_app.test_client().get(
            "/api/collector/configuration", headers={"Authorization": "Bearer " + "a" * 32}
        )
        assert response.status_code == 200
        assert "collector_mode" not in response.json


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
