"""Custo em consultas das leituras e gravações mais pesadas.

`/risk` buscava a série de cotações de cada ativo aberto com duas consultas
(permissão e série), e o número de idas ao banco crescia com a carteira. O
teste abaixo mede a página com duas e com seis posições abertas e exige a
mesma contagem: o N+1 não pode voltar sem reprovar aqui.

`upsert_quote_history` gravava uma instrução por linha; a importação "desde a
posição" traz anos de pregões por ticker. Os testes de lote medem que a
gravação agora vai em blocos e que o resultado é o mesmo da gravação linha a
linha — o instante mais novo vence, inclusive dentro do mesmo lote.

`/risk` precisa de dados confirmados (a requisição abre a própria sessão), então
aquele teste grava de verdade no `db-teste` e apaga o que criou ao sair, como
`test_financial_isolation_http.py`. Os de lote cabem na fixture `sessao`.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select

from app import db
from app.models import (
    Broker,
    Market,
    Portfolio,
    Position,
    PositionMovement,
    PositionMovementKind,
    QuoteHistory,
    Side,
    Ticker,
    User,
    UserPreference,
    UserTickerEntitlement,
)
from app.routes import helpers
from app.routes.helpers import upsert_quote_history

pytestmark = pytest.mark.banco

PREGOES = 5


@contextmanager
def contar_consultas(engine):
    """Conta as instruções SQL enviadas pelo `engine` dentro do bloco."""
    instrucoes: list[str] = []

    def registrar(conn, cursor, statement, parameters, context, executemany):
        instrucoes.append(statement)

    event.listen(engine, "before_cursor_execute", registrar)
    try:
        yield instrucoes
    finally:
        event.remove(engine, "before_cursor_execute", registrar)


@pytest.fixture
def carteira_de_risco(app_com_banco):
    """Usuário com carteira real, sem posições; `abrir` acrescenta ativos."""
    app = app_com_banco
    sufixo = uuid4().hex[:8]
    criados: dict[str, list[int]] = {"tickers": []}
    with app.app_context():
        usuario = User(username=f"perf-{sufixo}", role="operador", is_active_user=True,
                       must_change_password=False)
        usuario.set_password("Synthetic-perf-only-2026!")
        corretora = Broker(name=f"Perf {sufixo}", acronym=sufixo[:6])
        db.session.add_all([usuario, corretora])
        db.session.flush()
        carteira = Portfolio(owner_id=usuario.id, name=f"Perf {sufixo}", currency="BRL",
                             simulated=False)
        db.session.add(carteira)
        db.session.commit()
        criados.update(usuario=usuario.id, sessao=usuario.get_id(), corretora=corretora.id,
                       carteira=carteira.id)

    inicio = date.today() - timedelta(days=PREGOES + 1)

    def abrir(quantidade: int) -> list[str]:
        simbolos = []
        with app.app_context():
            for _ in range(quantidade):
                ticker = Ticker(symbol=f"Q{uuid4().hex[:10]}", trading_name="Ativo de medição",
                                market=Market.B3, currency="BRL", rtd_market_code="B")
                db.session.add(ticker)
                db.session.flush()
                criados["tickers"].append(ticker.id)
                simbolos.append(ticker.symbol)
                posicao = Position(owner_id=criados["usuario"], broker_id=criados["corretora"],
                                   ticker_id=ticker.id, portfolio_id=criados["carteira"],
                                   quantity=Decimal("10"), average_cost=Decimal("20"),
                                   quote_multiplier=1, target_multiplier=1, side=Side.BUY,
                                   opened_on=inicio, result_mode="L")
                db.session.add_all([
                    posicao,
                    UserTickerEntitlement(user_id=criados["usuario"], ticker_id=ticker.id,
                                          first_held_on=inicio),
                ])
                db.session.flush()
                db.session.add(PositionMovement(
                    owner_id=criados["usuario"], position_id=posicao.id,
                    kind=PositionMovementKind.OPEN, quantity_delta=Decimal("10"),
                    price=Decimal("20"), occurred_on=inicio, resulting_quantity=Decimal("10"),
                    resulting_average_cost=Decimal("20"),
                ))
                db.session.add_all(
                    QuoteHistory(ticker_id=ticker.id, price=Decimal(20 + dia),
                                 recorded_date=inicio + timedelta(days=dia),
                                 recorded_at=datetime.now(UTC))
                    for dia in range(PREGOES)
                )
            db.session.commit()
        return simbolos

    try:
        yield app, criados, abrir
    finally:
        with app.app_context():
            db.session.rollback()
            dono = criados["usuario"]
            for modelo in (PositionMovement, Position, Portfolio):
                db.session.execute(delete(modelo).where(modelo.owner_id == dono))
            for modelo in (UserPreference, UserTickerEntitlement):
                db.session.execute(delete(modelo).where(modelo.user_id == dono))
            db.session.execute(delete(QuoteHistory).where(
                QuoteHistory.ticker_id.in_(criados["tickers"])))
            db.session.execute(delete(Ticker).where(Ticker.id.in_(criados["tickers"])))
            db.session.execute(delete(Broker).where(Broker.id == criados["corretora"]))
            db.session.execute(delete(User).where(User.id == dono))
            db.session.commit()


def _cliente(app, sessao_id: str):
    cliente = app.test_client()
    sufixo = uuid4().hex
    cliente.environ_base["REMOTE_ADDR"] = "2001:db8::" + ":".join(
        sufixo[i:i + 4] for i in range(0, 16, 4)
    )
    assert cliente.get("/login").status_code == 200
    with cliente.session_transaction() as sessao:
        sessao["_user_id"] = sessao_id
        sessao["_fresh"] = True
    return cliente


def _medir_risco(app, cliente, simbolos: list[str]) -> int:
    with app.app_context():
        engine = db.engine
    with contar_consultas(engine) as instrucoes:
        resposta = cliente.get("/risk")
    assert resposta.status_code == 200
    pagina = resposta.get_data(as_text=True)
    for simbolo in simbolos:
        assert simbolo in pagina
    return len(instrucoes)


def test_risco_nao_consulta_o_banco_uma_vez_por_ativo(carteira_de_risco):
    app, criados, abrir = carteira_de_risco
    cliente = _cliente(app, criados["sessao"])
    simbolos = abrir(2)
    # A primeira visita cria a preferência do usuário sob demanda; medir a
    # partir da segunda deixa só o custo de regime.
    _medir_risco(app, cliente, simbolos)
    com_dois = _medir_risco(app, cliente, simbolos)

    simbolos += abrir(4)
    com_seis = _medir_risco(app, cliente, simbolos)

    assert com_seis == com_dois


def _linhas(sessao, ticker_id: int) -> list[tuple[date, Decimal, datetime]]:
    return [
        tuple(linha)
        for linha in sessao.execute(
            select(QuoteHistory.recorded_date, QuoteHistory.price, QuoteHistory.recorded_at)
            .where(QuoteHistory.ticker_id == ticker_id)
            .order_by(QuoteHistory.recorded_date)
        )
    ]


@pytest.fixture
def ticker_de_lote(sessao):
    ticker = Ticker(symbol=f"L{uuid4().hex[:10]}", trading_name="Ativo de lote",
                    market=Market.B3, currency="BRL", rtd_market_code="B")
    sessao.add(ticker)
    sessao.flush()
    return ticker.id


def _instante(hora: int) -> datetime:
    return datetime(2026, 9, 16, hora, tzinfo=UTC)


def test_lote_grava_em_blocos(sessao, ticker_de_lote, monkeypatch):
    monkeypatch.setattr(helpers, "QUOTE_HISTORY_UPSERT_BATCH_SIZE", 2)
    entradas = [
        (ticker_de_lote, Decimal(10 + dia), date(2026, 9, 1) + timedelta(days=dia), _instante(18))
        for dia in range(5)
    ]
    with contar_consultas(sessao.get_bind()) as instrucoes:
        upsert_quote_history(entradas)

    assert len(instrucoes) == 3
    assert [(dia, preco) for dia, preco, _ in _linhas(sessao, ticker_de_lote)] == [
        (data, preco) for _, preco, data, _ in entradas
    ]


def test_lote_vazio_nao_consulta(sessao):
    with contar_consultas(sessao.get_bind()) as instrucoes:
        upsert_quote_history([])
    assert instrucoes == []


def test_chave_repetida_no_mesmo_lote_fica_com_o_instante_mais_novo(sessao, ticker_de_lote):
    dia = date(2026, 9, 16)
    upsert_quote_history([
        (ticker_de_lote, Decimal("48.00"), dia, _instante(13)),
        (ticker_de_lote, Decimal("49.10"), dia, _instante(21)),
        # Mais velho que o anterior: gravado linha a linha, não substituiria.
        (ticker_de_lote, Decimal("47.00"), dia, _instante(17)),
        # Empate de instante: vale o último, como na gravação linha a linha.
        (ticker_de_lote, Decimal("49.20"), dia, _instante(21)),
    ])

    assert _linhas(sessao, ticker_de_lote) == [(dia, Decimal("49.20000000"), _instante(21))]


def test_lote_respeita_o_instante_de_cada_linha_existente(sessao, ticker_de_lote):
    antigo, recente = date(2026, 9, 15), date(2026, 9, 16)
    upsert_quote_history([
        (ticker_de_lote, Decimal("50.00"), antigo, _instante(13)),
        (ticker_de_lote, Decimal("48.71"), recente, _instante(21)),
    ])

    upsert_quote_history([
        # Mais novo que o gravado: substitui.
        (ticker_de_lote, Decimal("50.50"), antigo, _instante(20)),
        # Mais velho que a leitura do coletor: não substitui.
        (ticker_de_lote, Decimal("48.00"), recente, _instante(13)),
    ])

    assert _linhas(sessao, ticker_de_lote) == [
        (antigo, Decimal("50.50000000"), _instante(20)),
        (recente, Decimal("48.71000000"), _instante(21)),
    ]
