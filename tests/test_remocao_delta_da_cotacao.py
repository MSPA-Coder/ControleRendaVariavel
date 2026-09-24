"""A revisão que remove o delta da cotação recusa apagar um valor que importa."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

from app import create_app, db
from tests.conftest import _url_do_banco_de_teste

pytestmark = pytest.mark.banco

ANTERIOR = "20260913_0018"


@pytest.fixture
def app_na_revisao_anterior():
    """Cadeia Alembic em schema descartável, parada antes da remoção."""
    from flask_migrate import upgrade

    database_url = _url_do_banco_de_teste(administrativo=True)
    schema = f"delta_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    app = create_app(
        {"SQLALCHEMY_DATABASE_URI": f"{database_url}?options=-csearch_path%3D{schema}", "TESTING": True}
    )
    with app.app_context():
        upgrade(revision=ANTERIOR)
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _posicao_com_delta(app, delta: str) -> None:
    with app.app_context(), db.session.begin():
        db.session.execute(text("INSERT INTO users(id,username,password_hash,role,is_active_user,must_change_password) VALUES (1,'dono','hash','operador',true,false)"))
        db.session.execute(text("INSERT INTO brokers(id,name,acronym) VALUES (1,'Genial','GNL')"))
        db.session.execute(text("INSERT INTO tickers(id,symbol,trading_name,market,rtd_market_code,currency,is_benchmark,is_active) VALUES (1,'WEGE3','WEG','B3','B','BRL',false,true)"))
        db.session.execute(text("INSERT INTO portfolios(id,name,currency,simulated,is_active,owner_id) VALUES (1,'BRL','BRL',false,true,1)"))
        db.session.execute(
            text(
                """INSERT INTO positions(owner_id,broker_id,ticker_id,quantity,average_cost,side,opened_on,quote_multiplier,target_multiplier,result_mode,portfolio_id)
                VALUES (1,1,1,100,40,'BUY','2026-01-10',:delta,1.5,'L',1)"""
            ),
            {"delta": delta},
        )


def _colunas(app) -> set[str]:
    with app.app_context():
        return {coluna["name"] for coluna in inspect(db.engine).get_columns("positions")}


def test_delta_igual_a_um_e_removido_sem_mudar_a_posicao(app_na_revisao_anterior):
    from flask_migrate import upgrade

    _posicao_com_delta(app_na_revisao_anterior, "1")
    with app_na_revisao_anterior.app_context():
        upgrade()

    assert "quote_multiplier" not in _colunas(app_na_revisao_anterior)
    with app_na_revisao_anterior.app_context():
        quantidade, custo = db.session.execute(text("SELECT quantity, average_cost FROM positions")).one()
    assert (quantidade, custo) == (100, 40)


def test_banco_com_nomes_de_checagem_duplicados_tambem_migra(app_na_revisao_anterior):
    """O banco local foi adotado de um schema antigo e tem as checagens com o
    prefixo duplicado; a revisão não pode depender do nome delas."""
    from flask_migrate import upgrade

    _posicao_com_delta(app_na_revisao_anterior, "1")
    with app_na_revisao_anterior.app_context(), db.session.begin():
        for sufixo in ("positive", "finite"):
            db.session.execute(
                text(
                    f"ALTER TABLE positions RENAME CONSTRAINT ck_positions_quote_multiplier_{sufixo} "
                    f"TO ck_positions_ck_positions_quote_multiplier_{sufixo}"
                )
            )
    with app_na_revisao_anterior.app_context():
        upgrade()
        restantes = db.session.scalar(
            text(
                "SELECT count(*) FROM pg_constraint WHERE conrelid = 'positions'::regclass "
                "AND conname LIKE '%quote_multiplier%'"
            )
        )

    assert "quote_multiplier" not in _colunas(app_na_revisao_anterior)
    assert restantes == 0


def test_delta_diferente_de_um_aborta_a_revisao(app_na_revisao_anterior):
    """Apagar a coluna mudaria o valor dessa posição sem aviso."""
    from flask_migrate import upgrade

    _posicao_com_delta(app_na_revisao_anterior, "2")
    # O Flask-Migrate registra o erro da revisão e sai com código 1.
    with app_na_revisao_anterior.app_context(), pytest.raises(SystemExit):
        upgrade()

    assert "quote_multiplier" in _colunas(app_na_revisao_anterior)
