"""Custo em consultas das leituras e gravações mais pesadas.

`/risk` buscava a série de cotações de cada ativo aberto com duas consultas
(permissão e série), e o número de idas ao banco crescia com a carteira. O
teste abaixo mede a página com duas e com seis posições abertas e exige a
mesma contagem: o N+1 não pode voltar sem reprovar aqui. `/performance`
pagava duas consultas a mais pelo benchmark e uma por moeda nos proventos;
os testes dela exigem que nenhum dos dois acrescente consulta.

A varredura no fim estende a mesma exigência a todas as telas e à API de
patrimônio: com 2 e com 6 posições, cada uma com corretora, transação,
provento e opção próprios, nenhuma contagem pode crescer.

`upsert_quote_history` gravava uma instrução por linha; a importação "desde a
posição" traz anos de pregões por ticker. Os testes de lote medem que a
gravação agora vai em blocos e que o resultado é o mesmo da gravação linha a
linha — o instante mais novo vence, inclusive dentro do mesmo lote.

As páginas precisam de dados confirmados (a requisição abre a própria
sessão), então esses testes gravam de verdade no `db-teste` e apagam o que
criaram ao sair, como `test_financial_isolation_http.py`. Os de lote cabem na fixture `sessao`.
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
    ROLE_ADMIN,
    Broker,
    Dividend,
    Market,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionType,
    Portfolio,
    Position,
    PositionMovement,
    PositionMovementKind,
    QuoteHistory,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
    User,
    UserPreference,
    UserTickerEntitlement,
)
from app.quotes import history
from app.quotes.history import upsert_quote_history

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
def carteira_medida(app_com_banco):
    """Usuário com carteira real, sem posições; `abrir` acrescenta ativos."""
    app = app_com_banco
    sufixo = uuid4().hex[:8]
    criados: dict[str, list[int]] = {"tickers": [], "corretoras": [], "contratos": [], "vencimentos": []}
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

    def _ticker(moeda: str, *, referencia: bool = False) -> Ticker:
        mercado, codigo = (Market.B3, "B") if moeda == "BRL" else (Market.NYSE, "N")
        ticker = Ticker(symbol=f"Q{uuid4().hex[:10]}", trading_name="Ativo de medição",
                        market=mercado, currency=moeda, rtd_market_code=codigo,
                        is_benchmark=referencia)
        db.session.add(ticker)
        db.session.flush()
        criados["tickers"].append(ticker.id)
        db.session.add(UserTickerEntitlement(user_id=criados["usuario"], ticker_id=ticker.id,
                                             first_held_on=inicio))
        db.session.add_all(
            QuoteHistory(ticker_id=ticker.id, price=Decimal(20 + dia),
                         recorded_date=inicio + timedelta(days=dia),
                         recorded_at=datetime.now(UTC))
            for dia in range(PREGOES)
        )
        return ticker

    def _vencimento() -> int:
        if not criados["vencimentos"]:
            marca = uuid4().hex
            vencimento = OptionExpiration(
                call_code=marca[:5], put_code=marca[5:10],
                exercise_date=date.today() + timedelta(days=3000 + int(marca[:4], 16)),
            )
            db.session.add(vencimento)
            db.session.flush()
            criados["vencimentos"].append(vencimento.id)
        return criados["vencimentos"][0]

    def referencia() -> int:
        with app.app_context():
            ticker_id = _ticker("BRL", referencia=True).id
            db.session.commit()
        return ticker_id

    def abrir(quantidade: int, moeda: str = "BRL", *, historico: bool = False) -> list[str]:
        """Abre `quantidade` posições; com `historico`, cada uma ganha
        corretora própria, uma transação fechada e um provento -- o que faz
        um carregamento preguiçoso por linha aparecer na contagem."""
        simbolos = []
        with app.app_context():
            for _ in range(quantidade):
                ticker = _ticker(moeda)
                simbolos.append(ticker.symbol)
                corretora_id = criados["corretora"]
                if historico:
                    marca = uuid4().hex[:6]
                    corretora = Broker(name=f"Perf {marca}", acronym=marca)
                    db.session.add(corretora)
                    db.session.flush()
                    corretora_id = corretora.id
                    criados["corretoras"].append(corretora_id)
                    db.session.add(Transaction(
                        owner_id=criados["usuario"], broker_id=corretora_id,
                        ticker_id=ticker.id, portfolio_id=criados["carteira"],
                        quantity=Decimal("5"), average_cost=Decimal("20"),
                        exit_price=Decimal("22"), side=Side.BUY, opened_on=inicio,
                        closed_on=inicio + timedelta(days=1), result_mode="L",
                        result=Decimal("10"), status=TransactionStatus.CLOSED,
                    ))
                    db.session.add(Dividend(
                        owner_id=criados["usuario"], broker_id=corretora_id,
                        ticker_id=ticker.id, amount=Decimal("3"),
                        payment_date=inicio + timedelta(days=2),
                    ))
                    contrato = OptionContract(
                        ticker_id=_ticker(moeda).id, underlying_ticker_id=ticker.id,
                        expiration_id=_vencimento(), option_type=OptionType.CALL,
                        strike=Decimal("21"),
                    )
                    db.session.add(contrato)
                    db.session.flush()
                    criados["contratos"].append(contrato.id)
                    db.session.add(OptionPosition(
                        owner_id=criados["usuario"], broker_id=corretora_id,
                        contract_id=contrato.id, portfolio_id=criados["carteira"],
                        quantity=Decimal("100"), average_cost=Decimal("1"), side=Side.BUY,
                        opened_on=inicio, result_mode="L",
                    ))
                posicao = Position(owner_id=criados["usuario"], broker_id=corretora_id,
                                   ticker_id=ticker.id, portfolio_id=criados["carteira"],
                                   quantity=Decimal("10"), average_cost=Decimal("20"),
                                   target_multiplier=1, side=Side.BUY,
                                   opened_on=inicio, result_mode="L")
                db.session.add(posicao)
                db.session.flush()
                db.session.add(PositionMovement(
                    owner_id=criados["usuario"], position_id=posicao.id,
                    kind=PositionMovementKind.OPEN, quantity_delta=Decimal("10"),
                    price=Decimal("20"), occurred_on=inicio, resulting_quantity=Decimal("10"),
                    resulting_average_cost=Decimal("20"),
                ))
            db.session.commit()
        return simbolos

    try:
        yield app, criados, abrir, referencia
    finally:
        with app.app_context():
            db.session.rollback()
            dono = criados["usuario"]
            for modelo in (PositionMovement, Position, OptionPosition, Transaction, Dividend,
                           Portfolio):
                db.session.execute(delete(modelo).where(modelo.owner_id == dono))
            db.session.execute(delete(OptionContract).where(
                OptionContract.id.in_(criados["contratos"])))
            db.session.execute(delete(OptionExpiration).where(
                OptionExpiration.id.in_(criados["vencimentos"])))
            for modelo in (UserPreference, UserTickerEntitlement):
                db.session.execute(delete(modelo).where(modelo.user_id == dono))
            db.session.execute(delete(QuoteHistory).where(
                QuoteHistory.ticker_id.in_(criados["tickers"])))
            db.session.execute(delete(Ticker).where(Ticker.id.in_(criados["tickers"])))
            db.session.execute(delete(Broker).where(
                Broker.id.in_([criados["corretora"], *criados["corretoras"]])))
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


def _medir(app, cliente, url: str, simbolos: list[str] = ()) -> int:
    with app.app_context():
        engine = db.engine
    with contar_consultas(engine) as instrucoes:
        resposta = cliente.get(url)
    assert resposta.status_code == 200
    pagina = resposta.get_data(as_text=True)
    for simbolo in simbolos:
        assert simbolo in pagina
    return len(instrucoes)


def test_risco_nao_consulta_o_banco_uma_vez_por_ativo(carteira_medida):
    app, criados, abrir, _ = carteira_medida
    cliente = _cliente(app, criados["sessao"])
    simbolos = abrir(2)
    # A primeira visita cria a preferência do usuário sob demanda; medir a
    # partir da segunda deixa só o custo de regime.
    _medir(app, cliente, "/risk", simbolos)
    com_dois = _medir(app, cliente, "/risk", simbolos)

    simbolos += abrir(4)
    com_seis = _medir(app, cliente, "/risk", simbolos)

    assert com_seis == com_dois


def test_performance_nao_paga_consulta_extra_pelo_benchmark(carteira_medida):
    app, criados, abrir, referencia = carteira_medida
    cliente = _cliente(app, criados["sessao"])
    abrir(2)
    benchmark = referencia()
    _medir(app, cliente, "/performance")
    sem_benchmark = _medir(app, cliente, "/performance")

    com_benchmark = _medir(app, cliente, f"/performance?benchmark_ticker_id={benchmark}")

    assert com_benchmark == sem_benchmark


def test_performance_nao_consulta_proventos_uma_vez_por_moeda(carteira_medida):
    app, criados, abrir, _ = carteira_medida
    cliente = _cliente(app, criados["sessao"])
    # Sem o filtro explícito a página mostra uma moeda só, e a segunda nem
    # chegaria ao relatório.
    todas = "/performance?currency=ALL"
    abrir(1, "BRL")
    _medir(app, cliente, todas)
    uma_moeda = _medir(app, cliente, todas)

    abrir(1, "USD")
    duas_moedas = _medir(app, cliente, todas)

    assert duas_moedas == uma_moeda


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
    monkeypatch.setattr(history, "QUOTE_HISTORY_UPSERT_BATCH_SIZE", 2)
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



# Telas medidas pelo teste de varredura. Cada uma é pedida inteira e como
# fragmento HTMX: os dois caminhos montam contextos diferentes, e o N+1 de
# Transações só aparecia no fragmento.
TELAS = [
    "/", "/data-status", "/analysis/exposure-asset", "/analysis/exposure-broker",
    "/analysis/exposure-market", "/options", "/transactions", "/transactions?status=all",
    "/dividends", "/performance", "/risk", "/quotes", "/preferences", "/tables/portfolios",
    "/tables/brokers", "/tables/tickers", "/tables/options/contracts",
    "/tables/options/expirations", "/settings",
]
PATRIMONIO = [
    "/patrimonio/v1/resumo", "/patrimonio/v2/resumo", "/patrimonio/v3/activities",
    "/patrimonio/v3/categories", "/patrimonio/v3/events", "/patrimonio/v3/income",
    "/patrimonio/v3/performance",
]
TOKEN_PATRIMONIO = "token-de-medicao-com-mais-de-trinta-e-dois-caracteres"


def test_nenhuma_tela_consulta_o_banco_uma_vez_por_linha(carteira_medida):
    """Varredura: a contagem de consultas não cresce com a carteira.

    Com `historico`, cada posição nova traz corretora, transação, provento e
    opção próprios, então um carregamento preguiçoso por linha aparece como
    diferença entre 2 e 6 ativos. Achou três quando foi escrito: Transações
    (fragmento) e Proventos buscavam a corretora de cada linha, e
    Configurações checava a permissão de cada ticker cadastrado.
    """
    app, criados, abrir, _ = carteira_medida
    app.config.update(PATRIMONIO_TOKEN=TOKEN_PATRIMONIO, PATRIMONIO_TITULAR="Medição",
                      PATRIMONIO_OWNER_ID=str(criados["usuario"]))
    with app.app_context():
        # As tabelas e Configurações são exclusivas de admin.
        db.session.get(User, criados["usuario"]).role = ROLE_ADMIN
        db.session.commit()
        engine = db.engine
    cliente = _cliente(app, criados["sessao"])
    # A API de patrimônio é chamada por outro sistema, sem sessão de login.
    anonimo = app.test_client()
    pedidos = [(cliente, url, {}) for url in TELAS]
    pedidos += [(cliente, url, {"HX-Request": "true"}) for url in TELAS]
    pedidos += [(anonimo, url, {"Authorization": f"Bearer {TOKEN_PATRIMONIO}"})
                for url in PATRIMONIO]

    def medir_todas() -> dict[tuple[str, bool], int]:
        contagens = {}
        for quem, url, cabecalhos in pedidos:
            with contar_consultas(engine) as instrucoes:
                resposta = quem.get(url, headers=cabecalhos)
            assert resposta.status_code == 200, (url, resposta.status_code)
            contagens[(url, "HX-Request" in cabecalhos)] = len(instrucoes)
        return contagens

    abrir(2, historico=True)
    medir_todas()  # a primeira visita cria preferências sob demanda
    com_dois = medir_todas()
    abrir(4, historico=True)
    com_seis = medir_todas()

    crescimento = {
        chave: (com_dois[chave], com_seis[chave])
        for chave in com_dois
        if com_seis[chave] != com_dois[chave]
    }
    assert crescimento == {}
