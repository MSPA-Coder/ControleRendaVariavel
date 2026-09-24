"""O lançamento manual de cotação não pode ser sobrescrito silenciosamente.

Defeito de 17/09/2026: `create_quote_history_entry` carimbava o lançamento
manual à meia-noite UTC do dia informado. O upsert compartilhado
(`upsert_quote_history`) resolve conflito por `recorded_at >=` -- "a
observação mais tardia vence" --, e uma importação Yahoo de ação carimba a
barra do mesmo dia às 13:00 UTC (abertura da B3), sempre depois da meia-noite.
Resultado: qualquer importação para o mesmo dia sobrescrevia o preço que o
administrador acabara de digitar, sem aviso.

A correção troca `time.min` por `time.max`: o lançamento manual passa a ser o
último instante do dia, e só um outro lançamento manual (ou uma leitura RTD
carimbada depois da meia-noite do dia seguinte, que não existe) o alcança.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Market, QuoteHistory, Ticker
from app.quotes.history import upsert_quote_history


def _lancamento_manual(ticker_id: int, preco: str, dia: date) -> tuple[int, Decimal, date, datetime]:
    """Mesmo cálculo de `create_quote_history_entry` (app/routes/quotes.py)."""
    recorded_at = datetime.combine(dia, time.max, tzinfo=UTC)
    return (ticker_id, Decimal(preco), dia, recorded_at)


@pytest.fixture
def ticker_b3(sessao) -> Ticker:
    ticker = Ticker(
        symbol=f"T{uuid4().hex[:8].upper()}",
        trading_name="Ticker de teste",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    sessao.add(ticker)
    sessao.flush()
    return ticker


def _linha(sessao, ticker: Ticker, dia: date) -> tuple[Decimal, datetime]:
    linha = sessao.execute(
        select(QuoteHistory.price, QuoteHistory.recorded_at).where(
            QuoteHistory.ticker_id == ticker.id, QuoteHistory.recorded_date == dia
        )
    ).one()
    return (linha.price, linha.recorded_at)


@pytest.mark.banco
def test_importacao_yahoo_nao_sobrescreve_lancamento_manual_do_mesmo_dia(sessao, ticker_b3):
    dia = date(2026, 9, 16)
    upsert_quote_history([_lancamento_manual(ticker_b3.id, "48.71", dia)])

    # Importação Yahoo do mesmo dia, carimbada na abertura da B3 (13:00 UTC) --
    # sempre anterior ao último instante do dia usado pelo manual.
    barra_yahoo = (ticker_b3.id, Decimal("48.65"), dia, datetime(2026, 9, 16, 13, 0, tzinfo=UTC))
    upsert_quote_history([barra_yahoo])
    sessao.flush()

    assert _linha(sessao, ticker_b3, dia) == (
        Decimal("48.71000000"),
        datetime.combine(dia, time.max, tzinfo=UTC),
    )


@pytest.mark.banco
def test_leitura_rtd_do_mesmo_dia_nao_sobrescreve_lancamento_manual(sessao, ticker_b3):
    dia = date(2026, 9, 16)
    upsert_quote_history([_lancamento_manual(ticker_b3.id, "48.71", dia)])

    # RTD lê perto do fechamento (21:05 UTC), ainda antes do último instante do dia.
    leitura_rtd = (ticker_b3.id, Decimal("48.90"), dia, datetime(2026, 9, 16, 21, 5, tzinfo=UTC))
    upsert_quote_history([leitura_rtd])
    sessao.flush()

    assert _linha(sessao, ticker_b3, dia) == (
        Decimal("48.71000000"),
        datetime.combine(dia, time.max, tzinfo=UTC),
    )


@pytest.mark.banco
def test_segundo_lancamento_manual_do_mesmo_dia_corrige_o_primeiro(sessao, ticker_b3):
    dia = date(2026, 9, 16)
    upsert_quote_history([_lancamento_manual(ticker_b3.id, "48.71", dia)])
    upsert_quote_history([_lancamento_manual(ticker_b3.id, "50.00", dia)])
    sessao.flush()

    assert _linha(sessao, ticker_b3, dia) == (
        Decimal("50.00000000"),
        datetime.combine(dia, time.max, tzinfo=UTC),
    )
