"""Ensaios PostgreSQL da adoção de registros financeiros legados."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text

from app import create_app, db
from tests.conftest import _url_do_banco_de_teste

pytestmark = pytest.mark.banco


@pytest.fixture
def legacy_app():
    """Cadeia Alembic em schema descartável, sem tocar no schema da suíte."""
    from flask_migrate import upgrade

    # Este ensaio cria schemas e aplica migrações deliberadamente; ele usa o
    # papel administrativo. A fixture normal da suíte continua no papel
    # `investimentos_app`, igual ao contêiner web.
    database_url = _url_do_banco_de_teste(administrativo=True)
    schema = f"ownership_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = f"{database_url}?options=-csearch_path%3D{schema}"
    app = create_app({"SQLALCHEMY_DATABASE_URI": scoped_url, "TESTING": True})
    with app.app_context():
        upgrade(revision="20260904_0015")
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
        admin_engine.dispose()
        cleanup_engine = create_engine(database_url)
        with cleanup_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        cleanup_engine.dispose()


def _legacy_rows(app, username: str = "mspa") -> None:
    with app.app_context(), db.session.begin():
        db.session.execute(
            text("INSERT INTO users(username,password_hash,role,is_active_user,must_change_password) VALUES (:username,'hash','operador',true,false)"),
            {"username": username},
        )
        if username == "mspa":
            db.session.execute(
                text("INSERT INTO users(username,password_hash,role,is_active_user,must_change_password) VALUES ('sem_fatos','hash','operador',true,false)")
            )
        db.session.execute(text("INSERT INTO brokers(name,acronym) VALUES ('Teste','T')"))
        db.session.execute(text("INSERT INTO tickers(symbol,trading_name,market,rtd_market_code,currency,is_benchmark,is_active) VALUES ('TEST3','Teste','B3','B','BRL',false,true)"))
        db.session.execute(text("INSERT INTO tickers(symbol,trading_name,market,rtd_market_code,currency,is_benchmark,is_active) VALUES ('OPCT3','Opção','B3','B','BRL',false,true)"))
        db.session.execute(text("INSERT INTO tickers(symbol,trading_name,market,rtd_market_code,currency,is_benchmark,is_active) VALUES ('BENCH','Referência sem posição','B3','B','BRL',true,true)"))
        db.session.execute(text("UPDATE app_settings SET benchmark_ticker_id=3 WHERE id=1"))
        db.session.execute(text("INSERT INTO portfolios(name,currency,simulated,is_active) VALUES ('Principal','BRL',false,true)"))
        db.session.execute(text("INSERT INTO option_expirations(call_code,put_code,exercise_date) VALUES ('A2626','P2626','2026-12-18')"))
        db.session.execute(text("INSERT INTO option_contracts(ticker_id,underlying_ticker_id,expiration_id,option_type,strike) VALUES (2,1,1,'CALL',10)"))
        for opened_on, average_cost in (("2026-01-02", "10"), ("2026-01-03", "11")):
            db.session.execute(
                text("""INSERT INTO positions(broker_id,ticker_id,quantity,average_cost,side,opened_on,quote_multiplier,target_multiplier,result_mode,portfolio_id)
                VALUES (1,1,1,:cost,'BUY',:opened_on,1,1.5,'L',1)"""),
                {"cost": average_cost, "opened_on": opened_on},
            )
        db.session.execute(
            text("""INSERT INTO quotes(position_id,last_price,previous_close,instrument_status,source_status,observed_at)
            VALUES (1,10,9,'A','online',:old), (2,12,11,'A','online',:new)"""),
            {"old": datetime(2026, 1, 2, tzinfo=UTC), "new": datetime(2026, 1, 3, tzinfo=UTC)},
        )
        db.session.execute(text("""INSERT INTO option_positions(broker_id,contract_id,quantity,average_cost,target_price,side,opened_on,result_mode,portfolio_id)
            VALUES (1,1,2,3,4,'BUY','2026-01-04','L',1)"""))
        db.session.execute(text("""INSERT INTO option_positions(broker_id,contract_id,quantity,average_cost,target_price,side,opened_on,result_mode,portfolio_id)
            VALUES (1,1,1,4,5,'SELL','2026-01-05','L',1)"""))
        db.session.execute(text("""INSERT INTO option_quotes(option_position_id,last_price,previous_close,underlying_price,instrument_status,source_status,observed_at)
            VALUES (1,2,1,12,'A','online',:old), (2,4,3,14,'A','online',:new)"""), {"old": datetime(2026, 1, 4, tzinfo=UTC), "new": datetime(2026, 1, 5, tzinfo=UTC)})
        db.session.execute(text("""INSERT INTO transactions(broker_id,ticker_id,quantity,average_cost,exit_price,side,opened_on,closed_on,result_mode,result,status,portfolio_id)
            VALUES (1,1,1,10,12,'BUY','2026-01-02','2026-01-06','L',2,'CLOSED',1)"""))
        db.session.execute(text("INSERT INTO dividends(broker_id,ticker_id,amount,payment_date) VALUES (1,1,7,'2026-01-07')"))
        db.session.execute(text("""INSERT INTO position_movements(position_id,kind,quantity_delta,price,occurred_on,result,resulting_quantity,resulting_average_cost)
            VALUES (1,'OPEN',1,10,'2026-01-02',NULL,1,10)"""))
        db.session.execute(text("""INSERT INTO option_position_movements(option_position_id,kind,quantity_delta,price,occurred_on,result,resulting_quantity,resulting_average_cost)
            VALUES (1,'OPEN',2,3,'2026-01-04',NULL,2,3)"""))
        db.session.execute(text("""INSERT INTO position_ledger_archive(occurred_on,ticker_id,portfolio_id,broker_id,instrument,source_position_id,resulting_signed_quantity)
            VALUES ('2026-01-08',1,1,1,'stock',99,0)"""))


def test_legado_mspa_preserva_contagens_e_ultimo_snapshot(legacy_app) -> None:
    from flask_migrate import upgrade

    _legacy_rows(legacy_app)
    with legacy_app.app_context():
        roots = (
            "portfolios", "positions", "option_positions", "transactions",
            "dividends", "position_movements", "option_position_movements",
            "position_ledger_archive",
        )
        before = {
            table: db.session.scalar(
                text(f"SELECT jsonb_agg(to_jsonb(row) ORDER BY id) FROM {table} row")
            )
            for table in roots
        }
        # `upgrade()` abre outra conexão Alembic; encerra a leitura antes de
        # pedir locks DDL sobre as mesmas tabelas.
        db.session.commit()
        upgrade()
        owner_id = db.session.scalar(text("SELECT id FROM users WHERE username='mspa'"))
        after = {
            table: db.session.scalar(
                text(f"SELECT jsonb_agg((to_jsonb(row) - 'owner_id') ORDER BY id) FROM {table} row")
            )
            for table in roots
        }
        assert after == before
        for table in before:
            assert db.session.scalar(text(f"SELECT count(*) FROM {table} WHERE owner_id=:owner"), {"owner": owner_id}) == len(before[table])
        other_id = db.session.scalar(text("SELECT id FROM users WHERE username='sem_fatos'"))
        assert db.session.scalar(text("SELECT count(*) FROM user_ticker_entitlements WHERE user_id=:owner"), {"owner": other_id}) == 0
        assert db.session.execute(text("SELECT ticker_id,last_price,buy_price FROM quotes")).one() == (1, 12, 12)
        assert db.session.execute(text("SELECT contract_id,last_price,underlying_price FROM option_quotes")).one() == (1, 4, 14)
        assert db.session.scalar(text("SELECT count(*) FROM user_ticker_entitlements WHERE user_id=:owner AND ticker_id=1"), {"owner": owner_id}) == 1
        assert db.session.scalar(text("SELECT benchmark_ticker_id FROM user_preferences WHERE user_id=:owner"), {"owner": owner_id}) is None


def test_legado_sem_mspa_interrompe_antes_de_atribuir(legacy_app) -> None:
    from flask_migrate import upgrade

    _legacy_rows(legacy_app, username="outro")
    with legacy_app.app_context():
        before = db.session.scalar(text("SELECT count(*) FROM positions"))
        db.session.commit()
        with pytest.raises(SystemExit):
            upgrade()
        # A pré-condição falha antes de alterar a tabela ou redistribuir fatos.
        assert db.session.scalar(text("SELECT count(*) FROM positions")) == before
        assert db.session.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='positions' AND column_name='owner_id'")) == 0
