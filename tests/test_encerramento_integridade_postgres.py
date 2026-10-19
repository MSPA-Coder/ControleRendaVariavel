"""Encerramento, reversão HTTP e falha tardia no arquivo durável real."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import _url_do_banco_de_teste
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.exc import IntegrityError
from test_financial_isolation_http import client_for
from test_financial_isolation_http import review_case as review_case
from test_position_use_cases import _command

from app import db
from app.models import (
    OptionPosition,
    Position,
    PositionLedgerArchive,
    PositionMovementArchive,
    Side,
    Transaction,
    TransactionStatus,
)
from app.positions.use_cases import close_option, close_stock, register_option, register_stock

pytestmark = pytest.mark.banco


@pytest.fixture
def caso(review_case):
    app, dados = review_case
    yield app, dados
    with app.app_context():
        db.session.rollback()
        db.session.execute(delete(PositionMovementArchive).where(PositionMovementArchive.owner_id.in_(dados["user_ids"])))
        db.session.commit()


def funcoes(instrumento):
    if instrumento == "stock":
        return Position, register_stock, close_stock
    return OptionPosition, register_option, close_option


@pytest.mark.parametrize("instrumento", ["stock", "option"])
@pytest.mark.parametrize("lado,sinal", [(Side.BUY, 1), (Side.SELL, -1)])
def test_reverter_parcial_por_http_e_encerrar_preserva_quantidade_custo_e_resultado(caso, instrumento, lado, sinal):
    app, dados = caso
    modelo, registrar, encerrar = funcoes(instrumento)
    dono = dados["user_ids"][0]
    with app.app_context():
        comando = replace(_command(dados, instrumento), side=lado)
        pid = registrar(comando, owner_id=dono, token=uuid4().hex).position.id
        espelho_id = db.session.scalar(select(Transaction.id).where(Transaction.owner_id == dono))
        parcial = encerrar(pid, Decimal("12"), date.today(), Decimal("40"), owner_id=dono)
        tid = parcial.id
        assert parcial.result == Decimal(80 * sinal)
        assert db.session.get(modelo, pid).quantity == Decimal("60")
        assert db.session.get(Transaction, espelho_id).quantity == Decimal("60")
    cliente, token = client_for(app, dados["ids"][0])
    # O encerramento parcial não pode ser editado como uma transação independente.
    assert cliente.post(f"/transactions/{tid}", data={"csrf_token": token, "quantity": "999"}).status_code == 302
    assert cliente.post(f"/transactions/{tid}/delete", data={"csrf_token": token}).status_code == 302
    with app.app_context():
        posicao = db.session.get(modelo, pid)
        assert posicao.quantity == Decimal("100") and posicao.average_cost == Decimal("10")
        assert len(posicao.movements) == 1
        assert db.session.get(Transaction, tid) is None
        encerrada = encerrar(pid, Decimal("12"), date.today(), owner_id=dono)
        assert encerrada.id == espelho_id and encerrada.status is TransactionStatus.CLOSED
        assert encerrada.quantity == Decimal("100") and encerrada.result == Decimal(200 * sinal)
        assert db.session.get(modelo, pid) is None
        arquivo = db.session.scalars(select(PositionLedgerArchive).where(PositionLedgerArchive.owner_id == dono)
                                     .order_by(PositionLedgerArchive.id)).all()
        assert [a.resulting_signed_quantity for a in arquivo] == [Decimal(100 * sinal), Decimal("0")]


@pytest.mark.parametrize("instrumento", ["stock", "option"])
def test_erro_postgres_ao_arquivar_desfaz_encerramento_inteiro_e_permite_retry(caso, instrumento):
    app, dados = caso
    modelo, registrar, encerrar = funcoes(instrumento)
    dono = dados["user_ids"][0]
    engine = create_engine(_url_do_banco_de_teste(administrativo=True), hide_parameters=True)
    restricao = "qa_archive_" + uuid4().hex
    with app.app_context():
        pid = registrar(_command(dados, instrumento), owner_id=dono, token=uuid4().hex).position.id
        espelho_id = db.session.scalar(select(Transaction.id).where(Transaction.owner_id == dono))
        db.session.rollback()
        # NOT VALID preserva linhas anteriores e recusa apenas a escrita deste dono.
        # A falha ocorre no banco depois de fechar o espelho, na etapa de arquivo.
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE position_ledger_archive ADD CONSTRAINT {restricao} CHECK (owner_id <> {dono}) NOT VALID"))
            with pytest.raises(IntegrityError):
                encerrar(pid, Decimal("12"), date.today(), owner_id=dono)
            posicao = db.session.get(modelo, pid)
            espelho = db.session.get(Transaction, espelho_id)
            assert posicao.quantity == espelho.quantity == Decimal("100")
            assert espelho.status is TransactionStatus.OPEN and espelho.closed_on is None
            assert len(posicao.movements) == 1
            assert not db.session.scalar(select(PositionLedgerArchive.id).where(PositionLedgerArchive.owner_id == dono))
            assert not db.session.scalar(select(PositionMovementArchive.id).where(PositionMovementArchive.owner_id == dono))
        finally:
            db.session.rollback()
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE position_ledger_archive DROP CONSTRAINT IF EXISTS {restricao}"))
            engine.dispose()
        assert encerrar(pid, Decimal("12"), date.today(), owner_id=dono).id == espelho_id
        assert db.session.get(modelo, pid) is None


@pytest.mark.parametrize("quantidade,preco,data", [("0", "12", date.today()), ("101", "12", date.today()),
                                                 ("1", "-1", date.today()), ("1", "12", date(2020, 1, 1)),
                                                 ("NaN", "12", date.today()), ("Infinity", "12", date.today()),
                                                 ("1", "NaN", date.today()), ("1", "Infinity", date.today())])
def test_encerramento_de_acao_invalido_nao_altera_posicao_ou_espelho(caso, quantidade, preco, data):
    app, dados = caso
    dono = dados["user_ids"][0]
    with app.app_context():
        pid = register_stock(_command(dados, "stock"), owner_id=dono, token=uuid4().hex).position.id
        with pytest.raises(ValueError):
            close_stock(pid, Decimal(preco), data, Decimal(quantidade), owner_id=dono)
        assert db.session.get(Position, pid).quantity == Decimal("100")
        assert db.session.scalars(select(Transaction).where(Transaction.owner_id == dono)).one().status is TransactionStatus.OPEN


@pytest.mark.parametrize("instrumento", ["stock", "option"])
@pytest.mark.parametrize("lado,sinal", [(Side.BUY, -1), (Side.SELL, 1)])
def test_encerramento_a_preco_zero_continua_valido(caso, instrumento, lado, sinal):
    app, dados = caso
    modelo, registrar, encerrar = funcoes(instrumento)
    dono = dados["user_ids"][0]
    with app.app_context():
        pid = registrar(replace(_command(dados, instrumento), side=lado), owner_id=dono, token=uuid4().hex).position.id
        encerrada = encerrar(pid, Decimal("0"), date.today(), owner_id=dono)
        assert encerrada.status is TransactionStatus.CLOSED
        assert encerrada.exit_price == Decimal("0") and encerrada.result == Decimal(1000 * sinal)
        assert db.session.get(modelo, pid) is None
