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
from app.models import Broker, Market, Portfolio, Position, QuoteHistory, Side, Ticker, User
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
