"""Data de cada cotação importada do Yahoo, e por que o dia corrente fica de fora.

Defeito de 17/09/2026: `fetch_yahoo_daily_quotes` tirava a data do carimbo em
UTC. Para ações isso acerta, porque o Yahoo carimba a barra diária na abertura
do pregão (13:00 UTC na B3, 13:30/14:30 UTC em Nova York). O câmbio é carimbado
à meia-noite de Londres, e no horário de verão britânico isso é 23:00 UTC da
véspera: de abril a outubro cada taxa de `USDBRL=X` era gravada um dia antes, e
a base local acumulou 140 taxas em domingos. A data passou a ser a do fuso que
o Yahoo informa na própria série (`meta.exchangeTimezoneName`).

A importação também gravava o dia corrente com o preço do momento. No câmbio,
e em dia sem pregão, esse preço chega numa entrada extra carimbada com a hora
da consulta, POSTERIOR ao carimbo da barra definitiva; como
`upsert_quote_history` só substitui por instante igual ou mais novo, a parcial
ficava congelada. O dia corrente saiu da importação, para todos os tickers.

Os carimbos abaixo são os que o Yahoo devolveu em 17/09/2026.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Market, QuoteHistory, Ticker
from app.quotes import history_import
from app.quotes.history_import import (
    QuoteHistoryImportError,
    TickerImportTarget,
    fetch_yahoo_daily_quotes,
)
from app.routes.helpers import upsert_quote_history

CAMBIO = TickerImportTarget(1, "USDBRL=X", Market.NYSE, is_benchmark=True)
ACAO_B3 = TickerImportTarget(2, "PETR4", Market.B3)
ACAO_EUA = TickerImportTarget(3, "AAPL", Market.NYSE)

LONDRES = "Europe/London"
SAO_PAULO = "America/Sao_Paulo"
NOVA_YORK = "America/New_York"

# Bem depois de todas as barras dos cenários: nenhuma delas é o dia corrente.
MUITO_DEPOIS = datetime(2027, 1, 1, tzinfo=UTC)


def _instante(texto: str) -> datetime:
    return datetime.fromisoformat(texto).replace(tzinfo=UTC)


def _resposta(meta: dict[str, object], barras: list[tuple[str, float | None]]) -> dict:
    fechamentos = [fechamento for _, fechamento in barras]
    return {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "timestamp": [int(_instante(carimbo).timestamp()) for carimbo, _ in barras],
                    "indicators": {
                        "quote": [{"close": fechamentos}],
                        "adjclose": [{"adjclose": fechamentos}],
                    },
                }
            ],
            "error": None,
        }
    }


@pytest.fixture
def yahoo(monkeypatch):
    """Troca a rede por uma resposta fixa; devolve quem define essa resposta."""

    resposta: dict[str, object] = {}

    def responder(_pedido, timeout):
        return io.BytesIO(json.dumps(resposta["corpo"]).encode("utf-8"))

    monkeypatch.setattr(history_import, "urlopen", responder)

    def definir(fuso: str | None, barras: list[tuple[str, float | None]]) -> None:
        meta = {} if fuso is None else {"exchangeTimezoneName": fuso}
        resposta["corpo"] = _resposta(meta, barras)

    return definir


def _datas(cotacoes) -> list[date]:
    return [cotacao.recorded_date for cotacao in cotacoes]


# --------------------------------------------------------------------------
# A data é a do fuso da bolsa
# --------------------------------------------------------------------------


def test_cambio_no_verao_britanico_vai_para_o_dia_seguinte_ao_do_carimbo_utc(yahoo):
    yahoo(
        LONDRES,
        [
            ("2026-09-10T23:00:00", 5.1028),  # sexta, 11/09
            ("2026-09-13T23:00:00", 5.1112),  # segunda, 14/09 -- carimbo de domingo
            ("2026-09-14T23:00:00", 5.1380),  # terça, 15/09
            ("2026-09-15T23:00:00", 5.1429),  # quarta, 16/09
        ],
    )

    cotacoes = fetch_yahoo_daily_quotes(
        CAMBIO, date(2026, 9, 11), date(2026, 9, 16), now=MUITO_DEPOIS
    )

    assert _datas(cotacoes) == [
        date(2026, 9, 11),
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
    ]
    assert all(cotacao.recorded_date.isoweekday() <= 5 for cotacao in cotacoes)
    assert cotacoes[-1].price == Decimal("5.1429")
    # O instante gravado continua sendo o carimbo do Yahoo; só a data mudou.
    assert cotacoes[-1].recorded_at == _instante("2026-09-15T23:00:00")


def test_cambio_no_inverno_fica_no_proprio_dia(yahoo):
    yahoo(
        LONDRES,
        [
            ("2026-01-12T00:00:00", 5.3811),
            ("2026-01-13T00:00:00", 5.3902),
        ],
    )

    cotacoes = fetch_yahoo_daily_quotes(
        CAMBIO, date(2026, 1, 12), date(2026, 1, 13), now=MUITO_DEPOIS
    )

    assert _datas(cotacoes) == [date(2026, 1, 12), date(2026, 1, 13)]


def test_cambio_atravessa_o_fim_do_verao_britanico(yahoo):
    # O horário de verão britânico de 2026 termina no domingo, 25/10.
    yahoo(
        LONDRES,
        [
            ("2026-10-22T23:00:00", 5.40),  # sexta, 23/10, ainda no verão
            ("2026-10-26T00:00:00", 5.41),  # segunda, 26/10, já no inverno
        ],
    )

    cotacoes = fetch_yahoo_daily_quotes(
        CAMBIO, date(2026, 10, 23), date(2026, 10, 26), now=MUITO_DEPOIS
    )

    assert _datas(cotacoes) == [date(2026, 10, 23), date(2026, 10, 26)]


def test_b3_fica_no_dia_do_pregao(yahoo):
    yahoo(
        SAO_PAULO,
        [
            ("2026-09-15T13:00:00", 50.43),
            ("2026-09-16T13:00:00", 48.65),
        ],
    )

    cotacoes = fetch_yahoo_daily_quotes(
        ACAO_B3, date(2026, 9, 15), date(2026, 9, 16), now=MUITO_DEPOIS
    )

    assert _datas(cotacoes) == [date(2026, 9, 15), date(2026, 9, 16)]
    assert [cotacao.price for cotacao in cotacoes] == [Decimal("50.43"), Decimal("48.65")]


def test_nyse_fica_no_dia_do_pregao_no_verao_e_no_inverno(yahoo):
    yahoo(
        NOVA_YORK,
        [
            ("2026-01-12T14:30:00", 260.25),  # EST
            ("2026-09-16T13:30:00", 332.41),  # EDT
        ],
    )

    cotacoes = fetch_yahoo_daily_quotes(
        ACAO_EUA, date(2026, 1, 1), date(2026, 9, 16), now=MUITO_DEPOIS
    )

    assert _datas(cotacoes) == [date(2026, 1, 12), date(2026, 9, 16)]


# --------------------------------------------------------------------------
# Só entram dias já encerrados
# --------------------------------------------------------------------------


def test_dia_corrente_nao_e_importado(yahoo):
    # Formato real do câmbio no meio do dia: a barra de hoje vem sem
    # fechamento, e o preço do momento chega numa entrada extra com a hora da
    # consulta.
    yahoo(
        LONDRES,
        [
            ("2026-09-15T23:00:00", 5.1429),  # 16/09, encerrado
            ("2026-09-16T23:00:00", None),  # 17/09, em andamento
            ("2026-09-17T17:19:39", 5.1548),  # 17/09, preço do momento
        ],
    )

    cotacoes = fetch_yahoo_daily_quotes(
        CAMBIO, date(2026, 9, 16), date(2026, 9, 17), now=_instante("2026-09-17T17:20:00")
    )

    assert _datas(cotacoes) == [date(2026, 9, 16)]


def test_dia_corrente_da_b3_nao_e_importado(yahoo):
    # Na ação, o preço do momento vem na própria barra de hoje.
    yahoo(
        SAO_PAULO,
        [
            ("2026-09-16T13:00:00", 48.65),
            ("2026-09-17T13:00:00", 49.07),
        ],
    )

    cotacoes = fetch_yahoo_daily_quotes(
        ACAO_B3, date(2026, 9, 16), date(2026, 9, 17), now=_instante("2026-09-17T17:04:39")
    )

    assert _datas(cotacoes) == [date(2026, 9, 16)]


def test_dia_corrente_e_o_da_bolsa_e_nao_o_do_utc(yahoo):
    # 23:30 UTC de 17/09: ainda 17/09 em São Paulo, já 18/09 em Londres.
    agora = _instante("2026-09-17T23:30:00")

    yahoo(
        LONDRES,
        [
            ("2026-09-16T23:00:00", 5.1600),  # 17/09, encerrado em Londres
            ("2026-09-17T23:00:00", 5.1650),  # 18/09, em andamento
        ],
    )
    cambio = fetch_yahoo_daily_quotes(CAMBIO, date(2026, 9, 17), date(2026, 9, 18), now=agora)

    yahoo(
        SAO_PAULO,
        [
            ("2026-09-16T13:00:00", 48.65),
            ("2026-09-17T13:00:00", 49.20),  # pregão já fechado, mas é hoje
        ],
    )
    acao = fetch_yahoo_daily_quotes(ACAO_B3, date(2026, 9, 16), date(2026, 9, 18), now=agora)

    assert _datas(cambio) == [date(2026, 9, 17)]
    assert _datas(acao) == [date(2026, 9, 16)]


def test_consulta_em_dia_sem_pregao_nao_inventa_cotacao(yahoo):
    # Achado na base local: ações dos EUA com cotação em sábados, gravadas com
    # a hora em que a importação rodou.
    yahoo(
        NOVA_YORK,
        [
            ("2026-08-14T13:30:00", 89.02),  # sexta
            ("2026-08-15T11:59:17", 89.02),  # sábado, hora da consulta
        ],
    )

    cotacoes = fetch_yahoo_daily_quotes(
        ACAO_EUA, date(2026, 8, 14), date(2026, 8, 15), now=_instante("2026-08-15T11:59:18")
    )

    assert _datas(cotacoes) == [date(2026, 8, 14)]


# --------------------------------------------------------------------------
# Sem fuso, a série é recusada
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "meta",
    [
        {},
        {"exchangeTimezoneName": None},
        {"exchangeTimezoneName": ""},
        {"exchangeTimezoneName": "Nada/Existe"},
        {"exchangeTimezoneName": "../etc/passwd"},
        "não é um objeto",
    ],
)
def test_serie_sem_fuso_valido_e_recusada(monkeypatch, meta):
    corpo = _resposta({}, [("2026-09-15T23:00:00", 5.1429)])
    corpo["chart"]["result"][0]["meta"] = meta
    monkeypatch.setattr(
        history_import,
        "urlopen",
        lambda _pedido, timeout: io.BytesIO(json.dumps(corpo).encode("utf-8")),
    )

    with pytest.raises(QuoteHistoryImportError, match="timezone"):
        fetch_yahoo_daily_quotes(CAMBIO, date(2026, 9, 15), date(2026, 9, 16), now=MUITO_DEPOIS)


# --------------------------------------------------------------------------
# Com o upsert de verdade (PostgreSQL)
# --------------------------------------------------------------------------


@pytest.fixture
def ticker_no_banco(sessao):
    def criar(market: Market, *, is_benchmark: bool = False) -> Ticker:
        ticker = Ticker(
            symbol=f"T{uuid4().hex[:8].upper()}",
            trading_name="Ticker de teste",
            market=market,
            rtd_market_code="B" if market == Market.B3 else "N",
            currency="BRL" if market == Market.B3 else "USD",
            is_benchmark=is_benchmark,
        )
        sessao.add(ticker)
        sessao.flush()
        return ticker

    return criar


def _importar(ticker: Ticker, inicio: date, fim: date, agora: datetime) -> None:
    alvo = TickerImportTarget(ticker.id, ticker.symbol, ticker.market, ticker.is_benchmark)
    upsert_quote_history(
        (ticker.id, cotacao.price, cotacao.recorded_date, cotacao.recorded_at)
        for cotacao in fetch_yahoo_daily_quotes(alvo, inicio, fim, now=agora)
    )


def _linhas(sessao, ticker: Ticker) -> list[tuple[date, Decimal, datetime]]:
    return [
        tuple(linha)
        for linha in sessao.execute(
            select(QuoteHistory.recorded_date, QuoteHistory.price, QuoteHistory.recorded_at)
            .where(QuoteHistory.ticker_id == ticker.id)
            .order_by(QuoteHistory.recorded_date)
        )
    ]


@pytest.mark.banco
def test_importacao_do_dia_seguinte_grava_o_fechamento_do_cambio(sessao, yahoo, ticker_no_banco):
    cambio = ticker_no_banco(Market.NYSE, is_benchmark=True)

    # 17/09, meio da tarde: 17/09 ainda está em andamento.
    yahoo(
        LONDRES,
        [
            ("2026-09-15T23:00:00", 5.1429),
            ("2026-09-16T23:00:00", None),
            ("2026-09-17T17:19:39", 5.1548),
        ],
    )
    _importar(cambio, date(2026, 9, 16), date(2026, 9, 17), _instante("2026-09-17T17:20:00"))

    # 18/09, de manhã: 17/09 já tem fechamento, 18/09 está em andamento.
    yahoo(
        LONDRES,
        [
            ("2026-09-15T23:00:00", 5.1429),
            ("2026-09-16T23:00:00", 5.1600),
            ("2026-09-17T23:00:00", None),
            ("2026-09-18T11:59:00", 5.1700),
        ],
    )
    _importar(cambio, date(2026, 9, 16), date(2026, 9, 18), _instante("2026-09-18T12:00:00"))
    sessao.flush()

    # Se a parcial de 17/09 tivesse sido gravada às 17:19 UTC, o fechamento
    # (carimbado 23:00 UTC de 16/09) não a substituiria, e ficaria 5,1548.
    assert _linhas(sessao, cambio) == [
        (date(2026, 9, 16), Decimal("5.14290000"), _instante("2026-09-15T23:00:00")),
        (date(2026, 9, 17), Decimal("5.16000000"), _instante("2026-09-16T23:00:00")),
    ]


@pytest.mark.banco
def test_importacao_nao_toca_a_linha_do_dia_gravada_pelo_rtd(sessao, yahoo, ticker_no_banco):
    acao = ticker_no_banco(Market.B3)
    leitura_rtd = _instante("2026-09-17T17:04:00")
    # O coletor grava pelo mesmo upsert, com o instante da leitura.
    upsert_quote_history([(acao.id, Decimal("49.10"), date(2026, 9, 17), leitura_rtd)])

    yahoo(
        SAO_PAULO,
        [
            ("2026-09-16T13:00:00", 48.65),
            ("2026-09-17T13:00:00", 49.07),
        ],
    )
    _importar(acao, date(2026, 9, 16), date(2026, 9, 17), _instante("2026-09-17T17:20:00"))
    sessao.flush()

    assert _linhas(sessao, acao) == [
        (date(2026, 9, 16), Decimal("48.65000000"), _instante("2026-09-16T13:00:00")),
        (date(2026, 9, 17), Decimal("49.10000000"), leitura_rtd),
    ]
