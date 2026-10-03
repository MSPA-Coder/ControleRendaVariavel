"""A importação "desde a posição" pela CLI grava pelo mesmo upsert da tela.

Defeito de 17/09/2026: o comando `flask import-position-history` montava o
próprio `INSERT ... ON CONFLICT DO UPDATE`, sem o filtro que
`upsert_quote_history` recebeu no #57 -- a linha do dia só é substituída por
um instante igual ou mais novo. A rota `/quotes/import-position-history` e o
coletor RTD já gravavam pelo helper; a CLI não. Pela linha de comando, o
fechamento do Yahoo, carimbado na abertura do pregão (13:00 UTC na B3),
sobrescrevia a linha que o RTD tinha gravado mais tarde no mesmo dia.

O comando abre e confirma a própria transação, então este teste não cabe na
fixture `sessao`, que só serve a quem nunca faz commit: ele grava de verdade no
`db-teste` e apaga o que criou ao sair, como `test_financial_isolation_http.py`.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app import db
from app.models import (
    Broker,
    Market,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionType,
    Portfolio,
    Position,
    QuoteHistory,
    Side,
    Ticker,
    Transaction,
    TransactionStatus,
    User,
)
from app.quotes import history_import
from app.quotes.history import upsert_quote_history

pytestmark = pytest.mark.banco


def _instante(texto: str) -> datetime:
    return datetime.fromisoformat(texto).replace(tzinfo=UTC)


@pytest.fixture
def acao_em_carteira(app_com_banco):
    """Ação da B3 com posição aberta em 14/09/2026; devolve (id, símbolo)."""

    sufixo = uuid4().hex[:8].upper()
    with app_com_banco.app_context():
        usuario = User(username=f"cli-{sufixo}", role="operador", is_active_user=True)
        usuario.set_password("Synthetic-cli-only-2026!")
        corretora = Broker(name=f"CLI {sufixo}", acronym=sufixo[:6])
        ticker = Ticker(
            symbol=f"C{sufixo}",
            trading_name="Ação de teste",
            market=Market.B3,
            rtd_market_code="B",
            currency="BRL",
        )
        db.session.add_all([usuario, corretora, ticker])
        db.session.flush()
        carteira = Portfolio(
            owner_id=usuario.id, name=f"CLI-{sufixo}", currency="BRL", simulated=False
        )
        db.session.add(carteira)
        db.session.flush()
        db.session.add(
            Position(
                owner_id=usuario.id,
                broker_id=corretora.id,
                ticker_id=ticker.id,
                portfolio_id=carteira.id,
                quantity=Decimal("100"),
                average_cost=Decimal("48"),
                side=Side.BUY,
                # Data fixa: é ela que define o início do período pedido ao
                # Yahoo, e as barras abaixo continuam dentro dele em qualquer dia
                # em que a suíte rodar.
                opened_on=date(2026, 9, 14),
            )
        )
        db.session.commit()
        ids = {
            "usuario": usuario.id,
            "corretora": corretora.id,
            "ticker": ticker.id,
            "carteira": carteira.id,
        }
        simbolo = ticker.symbol
    try:
        yield ids["ticker"], simbolo
    finally:
        with app_com_banco.app_context():
            db.session.rollback()
            db.session.execute(delete(Position).where(Position.owner_id == ids["usuario"]))
            db.session.execute(delete(Portfolio).where(Portfolio.id == ids["carteira"]))
            # `quote_history` vai junto, em cascata.
            db.session.execute(delete(Ticker).where(Ticker.id == ids["ticker"]))
            db.session.execute(delete(Broker).where(Broker.id == ids["corretora"]))
            db.session.execute(delete(User).where(User.id == ids["usuario"]))
            db.session.commit()


@pytest.fixture
def yahoo(monkeypatch):
    """Troca a rede por barras fixas, só para o símbolo definido.

    O comando importa TODAS as posições e referências que achar, e o `db-teste`
    fica de pé entre execuções do `quality`. Respondendo a qualquer símbolo, uma
    sobra de outro teste receberia estas barras e ficaria com linhas que a
    limpeza daqui não apaga. Os demais recebem "sem série" e caem na lista de
    falhas do comando, sem gravar nada.
    """

    series: dict[str, list[tuple[str, float]]] = {}

    def responder(pedido, timeout):
        simbolo = unquote(urlsplit(pedido.full_url).path.rsplit("/", 1)[-1])
        barras = series.get(simbolo)
        if barras is None:
            corpo: dict = {"chart": {"result": None, "error": {"code": "Not Found"}}}
        else:
            fechamentos = [fechamento for _, fechamento in barras]
            corpo = {
                "chart": {
                    "result": [
                        {
                            "meta": {"exchangeTimezoneName": "America/Sao_Paulo"},
                            "timestamp": [
                                int(_instante(carimbo).timestamp()) for carimbo, _ in barras
                            ],
                            "indicators": {
                                "quote": [{"close": fechamentos}],
                                "adjclose": [{"adjclose": fechamentos}],
                            },
                        }
                    ],
                    "error": None,
                }
            }
        return io.BytesIO(json.dumps(corpo).encode("utf-8"))

    monkeypatch.setattr(history_import, "urlopen", responder)
    return series.__setitem__


def test_cli_nao_sobrescreve_a_linha_gravada_depois_do_fechamento_do_yahoo(
    app_com_banco, acao_em_carteira, yahoo
):
    ticker_id, simbolo = acao_em_carteira
    leitura_rtd = _instante("2026-09-16T21:05:00")  # 18:05 em Brasília
    with app_com_banco.app_context():
        upsert_quote_history(
            [
                # Importação anterior do mesmo pregão: mesmo carimbo, preço antigo.
                (ticker_id, Decimal("50.00"), date(2026, 9, 15), _instante("2026-09-15T13:00:00")),
                # O coletor grava pelo mesmo upsert, com o instante da leitura.
                (ticker_id, Decimal("48.71"), date(2026, 9, 16), leitura_rtd),
            ]
        )
        db.session.commit()

    yahoo(
        f"{simbolo}.SA",
        [
            ("2026-09-14T13:00:00", 48.20),
            ("2026-09-15T13:00:00", 50.43),
            ("2026-09-16T13:00:00", 48.65),
        ],
    )

    with app_com_banco.app_context():
        resultado = app_com_banco.test_cli_runner().invoke(args=["import-position-history"])
        assert resultado.exit_code == 0, resultado.output
        linhas = [
            tuple(linha)
            for linha in db.session.execute(
                select(QuoteHistory.recorded_date, QuoteHistory.price, QuoteHistory.recorded_at)
                .where(QuoteHistory.ticker_id == ticker_id)
                .order_by(QuoteHistory.recorded_date)
            )
        ]

    assert linhas == [
        # Dia sem linha: entra.
        (date(2026, 9, 14), Decimal("48.20"), _instante("2026-09-14T13:00:00")),
        # Mesmo carimbo: o preço é atualizado.
        (date(2026, 9, 15), Decimal("50.43"), _instante("2026-09-15T13:00:00")),
        # Leitura posterior ao carimbo do Yahoo: fica.
        (date(2026, 9, 16), Decimal("48.71"), leitura_rtd),
    ]


@pytest.fixture
def acao_encerrada(app_com_banco):
    """Ação da B3 detida só em agosto de 2026; devolve o símbolo.

    Sem posição aberta, só a operação encerrada: o período dela termina no
    encerramento, e é isso que a separa do ticker que ainda importa hoje.
    """

    sufixo = uuid4().hex[:8].upper()
    with app_com_banco.app_context():
        usuario = User(username=f"cli-enc-{sufixo}", role="operador", is_active_user=True)
        usuario.set_password("Synthetic-cli-only-2026!")
        corretora = Broker(name=f"CLI ENC {sufixo}", acronym=sufixo[:6])
        ticker = Ticker(
            symbol=f"E{sufixo}",
            trading_name="Ação encerrada de teste",
            market=Market.B3,
            rtd_market_code="B",
            currency="BRL",
        )
        db.session.add_all([usuario, corretora, ticker])
        db.session.flush()
        carteira = Portfolio(
            owner_id=usuario.id, name=f"CLI-ENC-{sufixo}", currency="BRL", simulated=False
        )
        db.session.add(carteira)
        db.session.flush()
        db.session.add(
            Transaction(
                owner_id=usuario.id,
                broker_id=corretora.id,
                ticker_id=ticker.id,
                portfolio_id=carteira.id,
                quantity=Decimal("10"),
                average_cost=Decimal("20"),
                exit_price=Decimal("22"),
                side=Side.BUY,
                opened_on=date(2026, 8, 3),
                closed_on=date(2026, 8, 20),
                result=Decimal("20"),
                status=TransactionStatus.CLOSED,
            )
        )
        db.session.commit()
        ids = {
            "usuario": usuario.id,
            "corretora": corretora.id,
            "ticker": ticker.id,
            "carteira": carteira.id,
        }
        simbolo = ticker.symbol
    try:
        yield simbolo
    finally:
        with app_com_banco.app_context():
            db.session.rollback()
            db.session.execute(delete(Transaction).where(Transaction.owner_id == ids["usuario"]))
            db.session.execute(delete(Portfolio).where(Portfolio.id == ids["carteira"]))
            db.session.execute(delete(Ticker).where(Ticker.id == ids["ticker"]))
            db.session.execute(delete(Broker).where(Broker.id == ids["corretora"]))
            db.session.execute(delete(User).where(User.id == ids["usuario"]))
            db.session.commit()


def test_estrito_reprova_quando_um_ticker_ainda_detido_fica_sem_serie(
    app_com_banco, acao_em_carteira, yahoo
):
    """Quem roda sem ninguém olhando precisa de um código de saída que diga a verdade.

    O timer diário do `manutencao` chama o comando com `--estrito`. Sem a
    opção, um Yahoo fora do ar só deixava uma linha no stderr e a execução
    agendada terminava com sucesso, então o alerta nunca disparava. Sem ela o
    comportamento continua o de antes, para quem roda à mão.
    """

    _ticker_id, simbolo = acao_em_carteira
    # Nenhuma série cadastrada no `yahoo`: o símbolo detido recebe "sem série".
    with app_com_banco.app_context():
        runner = app_com_banco.test_cli_runner()
        sem_opcao = runner.invoke(args=["import-position-history"])
        estrito = runner.invoke(args=["import-position-history", "--estrito"])

    assert sem_opcao.exit_code == 0, sem_opcao.output
    assert estrito.exit_code == 1, estrito.output
    assert simbolo in estrito.stderr.splitlines()[-1]


def test_estrito_nao_reprova_por_ticker_ja_encerrado_sem_serie(
    app_com_banco, acao_encerrada, yahoo
):
    """Ativo encerrado que o Yahoo deixou de servir não vira alerta diário.

    A série dele já foi gravada enquanto era detido. Reprovar por ele todo dia
    ensinaria a ignorar o alerta do timer. Ele continua listado entre as falhas,
    mas fica fora da linha que decide o código de saída.

    O `db-teste` é compartilhado e o comando importa tudo o que achar, então o
    teste confere a linha do `--estrito`, e não só o código de saída: outro
    ticker detido que tenha sobrado de um teste anterior apareceria ali.
    """

    simbolo = acao_encerrada
    with app_com_banco.app_context():
        resultado = app_com_banco.test_cli_runner().invoke(
            args=["import-position-history", "--estrito"]
        )

    linhas = resultado.stderr.splitlines()
    falhas = next(linha for linha in linhas if linha.startswith("No Yahoo history for:"))
    assert simbolo in falhas
    assert not any(simbolo in linha for linha in linhas if linha.startswith("Error:"))


@pytest.fixture
def opcao_em_carteira(app_com_banco):
    """Opção de compra da B3 com posição aberta; devolve o símbolo dela.

    É o caso real de 02/10/2026: AUREL110, RAIZH150 e outras três, compradas e
    sem série no Yahoo. O ativo-objeto fica de fora da carteira, porque o que
    se quer provar é que a opção sozinha não reprova o `--estrito`.
    """

    sufixo = uuid4().hex[:8].upper()
    with app_com_banco.app_context():
        usuario = User(username=f"cli-opc-{sufixo}", role="operador", is_active_user=True)
        usuario.set_password("Synthetic-cli-only-2026!")
        corretora = Broker(name=f"CLI OPC {sufixo}", acronym=sufixo[:6])
        opcao = Ticker(
            symbol=f"O{sufixo}",
            trading_name="Opção de teste",
            market=Market.B3,
            rtd_market_code="B",
            currency="BRL",
        )
        objeto = Ticker(
            symbol=f"U{sufixo}",
            trading_name="Objeto de teste",
            market=Market.B3,
            rtd_market_code="B",
            currency="BRL",
        )
        # `call_code`/`put_code` têm 5 caracteres e `exercise_date` é única.
        vencimento = OptionExpiration(
            call_code=sufixo[:5], put_code=sufixo[3:], exercise_date=date(2031, 12, 19)
        )
        db.session.add_all([usuario, corretora, opcao, objeto, vencimento])
        db.session.flush()
        carteira = Portfolio(
            owner_id=usuario.id, name=f"CLI-OPC-{sufixo}", currency="BRL", simulated=False
        )
        contrato = OptionContract(
            ticker_id=opcao.id,
            underlying_ticker_id=objeto.id,
            expiration_id=vencimento.id,
            option_type=OptionType.CALL,
            strike=Decimal("10"),
        )
        db.session.add_all([carteira, contrato])
        db.session.flush()
        db.session.add(
            OptionPosition(
                owner_id=usuario.id,
                broker_id=corretora.id,
                contract_id=contrato.id,
                portfolio_id=carteira.id,
                quantity=Decimal("100"),
                average_cost=Decimal("1"),
                side=Side.BUY,
                opened_on=date(2026, 9, 14),
            )
        )
        db.session.commit()
        ids = {
            "usuario": usuario.id,
            "corretora": corretora.id,
            "opcao": opcao.id,
            "objeto": objeto.id,
            "vencimento": vencimento.id,
            "contrato": contrato.id,
            "carteira": carteira.id,
        }
        simbolo = opcao.symbol
    try:
        yield simbolo
    finally:
        with app_com_banco.app_context():
            db.session.rollback()
            db.session.execute(
                delete(OptionPosition).where(OptionPosition.owner_id == ids["usuario"])
            )
            db.session.execute(delete(OptionContract).where(OptionContract.id == ids["contrato"]))
            db.session.execute(
                delete(OptionExpiration).where(OptionExpiration.id == ids["vencimento"])
            )
            db.session.execute(delete(Portfolio).where(Portfolio.id == ids["carteira"]))
            db.session.execute(
                delete(Ticker).where(Ticker.id.in_([ids["opcao"], ids["objeto"]]))
            )
            db.session.execute(delete(Broker).where(Broker.id == ids["corretora"]))
            db.session.execute(delete(User).where(User.id == ids["usuario"]))
            db.session.commit()


def test_estrito_nao_reprova_por_opcao_aberta_que_o_yahoo_nao_serve(
    app_com_banco, opcao_em_carteira, yahoo
):
    """Opção da B3 não existe no Yahoo e a série dela vem do coletor RTD.

    Com a opção como alvo, o `--estrito` reprovava todo dia útil, o timer do
    `manutencao` acionava o Telegram, e o alarme não tinha como se apagar. Como
    o `db-teste` é compartilhado, o teste confere se o símbolo da opção sumiu
    da lista de falhas e da linha `Error:`, e não só o código de saída.
    """

    simbolo = opcao_em_carteira
    with app_com_banco.app_context():
        resultado = app_com_banco.test_cli_runner().invoke(
            args=["import-position-history", "--estrito"]
        )

    linhas = resultado.stderr.splitlines()
    assert not any(simbolo in linha for linha in linhas), resultado.stderr
