"""Travas de banco: cada teste segura o estado em outra transação.

Padrão: a "primeira operação" muda o estado e fica aberta (sem confirmar). A operação testada precisa
esperar por ela e então enxergar o estado confirmado. Sem a trava, a operação testada decide com o
estado antigo e o resultado final é errado. Não depende de sorte de tempo: a primeira operação só
libera quando a segunda já esperou ou terminou.

Cada teste chama o módulo interno (`positions.closure`, `options.closure`, `accounts.users`) direto,
sem passar por `use_cases`, porque `use_cases` já toma a trava por dono (`lock_executions`) e esconderia
a trava de dentro. A trava por dono e a de dentro são camadas; aqui se prova a de dentro.
"""

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import text, update

from app import db
from app.accounts import users
from app.models import OptionPosition, Position, Side, User
from app.options import closure as options
from app.positions import closure as stocks
from app.positions.commands import OptionPositionInput, PositionInput
from app.positions.use_cases import close_option, close_stock, register_option, register_stock
from tests.test_financial_isolation_http import review_case as review_case

pytestmark = pytest.mark.banco

INSTRUMENTOS = ["stock", "option"]


def _command(data, instrument):
    fields = {
        "broker_id": data["broker"], "portfolio_id": data["portfolio_ids"][0],
        "quantity": Decimal("100"), "average_cost": Decimal("10"),
        "side": Side.BUY, "opened_on": date(2026, 6, 10),
    }
    if instrument == "stock":
        return PositionInput(**fields, ticker_id=data["ticker_ids"][0], target_multiplier=Decimal("1.5"))
    return OptionPositionInput(**fields, contract_id=data["contract"], target_price=None)


def _modulo(instrument):
    return stocks if instrument == "stock" else options


def _modelo(instrument):
    return Position if instrument == "stock" else OptionPosition


def _com_primeira_operacao_aberta(app, testada, mexer_no_estado):
    """Roda `testada` enquanto `mexer_no_estado()` segura o estado; devolve (resultado, erro) da testada."""
    preparada, liberar = Event(), Event()

    def primeira():
        with app.app_context():
            try:
                mexer_no_estado()
                preparada.set()
                assert liberar.wait(timeout=10)
                db.session.commit()
            finally:
                db.session.remove()

    def segunda():
        with app.app_context():
            try:
                resultado = testada()
                db.session.commit()
                return resultado, None
            except Exception as erro:  # noqa: BLE001 - o teste confere o tipo
                db.session.rollback()
                return None, erro
            finally:
                db.session.remove()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(primeira)
        assert preparada.wait(timeout=10)
        b = pool.submit(segunda)
        # Com a trava a segunda continua esperando; sem ela já terminou.
        wait([b], timeout=1.5)
        liberar.set()
        a.result(timeout=10)
        return b.result(timeout=10)


def _quantidade(app, instrument, position_id):
    with app.app_context():
        db.session.expire_all()
        return db.session.get(_modelo(instrument), position_id).quantity


def _posicao_com_100(app, data, instrument):
    owner_id = data["user_ids"][0]
    registrar = register_stock if instrument == "stock" else register_option
    with app.app_context():
        return registrar(_command(data, instrument), owner_id=owner_id, token=uuid4().hex).position.id, owner_id


def _somar_na_linha(instrument, position_id, quantidade):
    """Outra transação que altera a linha da posição e não confirma."""
    modelo = _modelo(instrument)
    db.session.execute(update(modelo).where(modelo.id == position_id).values(quantity=modelo.quantity + quantidade))


@pytest.mark.parametrize("instrument", INSTRUMENTOS)
def test_encerramento_espera_a_linha_da_posicao_e_usa_a_quantidade_nova(review_case, instrument):
    app, data = review_case
    position_id, owner_id = _posicao_com_100(app, data, instrument)
    modulo = _modulo(instrument)

    _, erro = _com_primeira_operacao_aberta(
        app,
        lambda: modulo.apply_position_closure(position_id, Decimal("12"), date.today(), Decimal("120"), owner_id=owner_id),
        lambda: _somar_na_linha(instrument, position_id, Decimal("50")),
    )

    assert erro is None
    assert _quantidade(app, instrument, position_id) == Decimal("30")


@pytest.mark.parametrize("instrument", INSTRUMENTOS)
def test_mesclagem_espera_a_linha_da_posicao_e_soma_sobre_a_quantidade_nova(review_case, instrument):
    app, data = review_case
    position_id, owner_id = _posicao_com_100(app, data, instrument)
    modulo = _modulo(instrument)
    comando = replace(_command(data, instrument), quantity=Decimal("25"))
    modelo = _modelo(instrument)

    def mesclar():
        candidata = modelo(owner_id=owner_id, **asdict(comando))
        return modulo.create_or_merge_position(candidata)

    _, erro = _com_primeira_operacao_aberta(
        app, mesclar, lambda: _somar_na_linha(instrument, position_id, Decimal("50"))
    )

    assert erro is None
    assert _quantidade(app, instrument, position_id) == Decimal("175")


@pytest.mark.parametrize("instrument", INSTRUMENTOS)
def test_mesclagem_espera_a_trava_da_chave_da_posicao(review_case, instrument):
    app, data = review_case
    position_id, owner_id = _posicao_com_100(app, data, instrument)
    modulo = _modulo(instrument)
    modelo = _modelo(instrument)
    comando = replace(_command(data, instrument), quantity=Decimal("25"))
    candidata = modelo(owner_id=owner_id, **asdict(comando))
    chave = (
        stocks._chave(candidata) if instrument == "stock"
        else f"opcao:{candidata.owner_id}:{candidata.portfolio_id}:{candidata.broker_id}:{candidata.contract_id}:{candidata.side}"
    )

    def segurar_a_chave():
        db.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:chave, 0))"), {"chave": chave})

    def mesclar():
        return modulo.create_or_merge_position(modelo(owner_id=owner_id, **asdict(comando)))

    preparada, liberar = Event(), Event()

    def primeira():
        with app.app_context():
            try:
                segurar_a_chave()
                preparada.set()
                assert liberar.wait(timeout=10)
                db.session.commit()
            finally:
                db.session.remove()

    def segunda():
        with app.app_context():
            try:
                mesclar()
                db.session.commit()
            finally:
                db.session.remove()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(primeira)
        assert preparada.wait(timeout=10)
        b = pool.submit(segunda)
        terminou_sem_esperar = bool(wait([b], timeout=1.5).done)
        liberar.set()
        a.result(timeout=10)
        b.result(timeout=20)

    assert not terminou_sem_esperar
    assert _quantidade(app, instrument, position_id) == Decimal("125")


@pytest.fixture
def dois_administradores(review_case):
    app, _ = review_case
    with app.app_context():
        db.session.execute(update(User).values(role="operador"))
        nomes = []
        for i in (1, 2):
            usuario = User(username=f"adm-{uuid4().hex[:8]}-{i}", role="admin", is_active_user=True,
                           must_change_password=False)
            usuario.set_password("Synthetic-review-only-2026!")
            db.session.add(usuario)
            nomes.append(usuario)
        db.session.commit()
        return app, [(u.id, u.username) for u in nomes]


def _admins_ativos(app):
    with app.app_context():
        db.session.expire_all()
        return db.session.query(User).filter_by(role="admin", is_active_user=True).count()


def test_rebaixar_o_ultimo_administrador_espera_e_e_recusado(dois_administradores):
    app, [(primeiro, _), (segundo, nome_do_segundo)] = dois_administradores

    def rebaixar_o_primeiro():
        users._lock_admin_mutations()
        db.session.execute(update(User).where(User.id == primeiro).values(role="operador"))

    _, erro = _com_primeira_operacao_aberta(
        app, lambda: users.update_user(segundo, nome_do_segundo, "operador"), rebaixar_o_primeiro
    )

    assert isinstance(erro, users.LastAdminError)
    assert _admins_ativos(app) == 1


def test_desativar_o_ultimo_administrador_espera_e_e_recusado(dois_administradores):
    app, [(primeiro, _), (segundo, _)] = dois_administradores

    def rebaixar_o_primeiro():
        users._lock_admin_mutations()
        db.session.execute(update(User).where(User.id == primeiro).values(role="operador"))

    _, erro = _com_primeira_operacao_aberta(
        app, lambda: users.set_active(segundo, False), rebaixar_o_primeiro
    )

    assert isinstance(erro, users.LastAdminError)
    assert _admins_ativos(app) == 1


@pytest.mark.parametrize("instrument", INSTRUMENTOS)
def test_reverter_encerramento_parcial_espera_a_linha_da_posicao(review_case, instrument):
    app, data = review_case
    position_id, owner_id = _posicao_com_100(app, data, instrument)
    fechar = close_stock if instrument == "stock" else close_option
    with app.app_context():
        transacao = fechar(position_id, Decimal("12"), date.today(), Decimal("40"), owner_id=owner_id)
        transacao_id = transacao.id
    modulo = _modulo(instrument)
    from app.models import Transaction

    modelo = _modelo(instrument)
    comando = replace(_command(data, instrument), quantity=Decimal("50"))

    def aporte_de_50():
        modulo.create_or_merge_position(modelo(owner_id=owner_id, **asdict(comando)))

    def reverter():
        return modulo.revert_partial_close(db.session.get(Transaction, transacao_id))

    _, erro = _com_primeira_operacao_aberta(
        app, reverter, aporte_de_50
    )

    assert erro is None
    assert _quantidade(app, instrument, position_id) == Decimal("150")
