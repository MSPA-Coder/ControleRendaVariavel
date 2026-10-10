"""O outbox do contrato v4 não cresce à toa, e o expurgo não cala aviso.

Em 10/10/2026 o `patrimonio_v4_outbox` de produção tinha 115 mil linhas em 12
dias. 85% vinham da importação diária, que regravava o histórico inteiro de
cotações sem nenhum preço mudar, e cada linha regravada virava uma invalidação.
O expurgo de 30 dias (`app/patrimonio/outbox.py`) só é seguro porque a linha
mais nova de cada dono fica: é ela que faz um checkpoint antigo continuar
vendo que houve mudança. Estes testes medem as duas coisas no PostgreSQL, que é
onde vive o gatilho.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import Market, PatrimonioV4Outbox, QuoteHistory, Ticker, User
from app.patrimonio.outbox import expurgar_outbox_v4
from app.quotes.history import upsert_quote_history
from app.routes import patrimonio as patrimonio_route

pytestmark = pytest.mark.banco

TOKEN_V4 = "token-de-integracao-v4-com-mais-de-trinta-e-dois-caracteres"
AGORA = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)


@pytest.fixture
def papel(sessao):
    ticker = Ticker(
        symbol="WEGE3",
        trading_name="WEG S.A.",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    sessao.add(ticker)
    sessao.flush()
    return ticker


def _invalidacoes_de_historico(sessao) -> int:
    return sessao.scalar(
        select(func.count()).where(PatrimonioV4Outbox.resource == "price_history")
    )


def test_reimportar_o_mesmo_preco_nao_emite_invalidacao(sessao, papel):
    dia = date(2026, 10, 9)
    primeira = datetime(2026, 10, 9, 21, 0, tzinfo=UTC)
    upsert_quote_history([(papel.id, Decimal("52.10"), dia, primeira)])
    sessao.flush()
    depois_da_primeira = _invalidacoes_de_historico(sessao)

    # A importação do dia seguinte traz o mesmo fechamento, com instante novo.
    upsert_quote_history([(papel.id, Decimal("52.10"), dia, primeira + timedelta(hours=10))])
    sessao.flush()

    assert _invalidacoes_de_historico(sessao) == depois_da_primeira
    linha = sessao.scalar(select(QuoteHistory).where(QuoteHistory.ticker_id == papel.id))
    sessao.refresh(linha)
    assert linha.recorded_at == primeira


def test_preco_novo_no_mesmo_dia_regrava_e_emite_invalidacao(sessao, papel):
    dia = date(2026, 10, 9)
    primeira = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
    upsert_quote_history([(papel.id, Decimal("52.10"), dia, primeira)])
    sessao.flush()
    antes = _invalidacoes_de_historico(sessao)

    upsert_quote_history([(papel.id, Decimal("53.00"), dia, primeira + timedelta(hours=1))])
    sessao.flush()

    assert _invalidacoes_de_historico(sessao) == antes + 1
    linha = sessao.scalar(select(QuoteHistory).where(QuoteHistory.ticker_id == papel.id))
    sessao.refresh(linha)
    assert linha.price == Decimal("53.00")


def _reservar_cursores(sessao, quantidade: int) -> int:
    """Avança o contador como o gatilho faria e devolve o primeiro cursor livre.
    O `/changes` só publica cursores até o contador (o `high_watermark`)."""
    fim = sessao.execute(
        text(
            "UPDATE patrimonio_v4_change_counter SET value = value + :n "
            "WHERE id = 1 RETURNING value"
        ),
        {"n": quantidade},
    ).scalar_one()
    return fim - quantidade + 1


def _evento(cursor: int, dono: int | None, idade_dias: int) -> PatrimonioV4Outbox:
    return PatrimonioV4Outbox(
        cursor=cursor,
        owner_id=dono,
        resource="holding" if dono is not None else "price_current",
        source_record_id=cursor,
        operation="upsert",
        changed_at=AGORA - timedelta(days=idade_dias),
    )


@pytest.fixture
def donos(sessao):
    a = User(username="dono_a", password_hash="hash")
    b = User(username="dono_b", password_hash="hash")
    sessao.add_all([a, b])
    sessao.flush()
    return a, b


@pytest.fixture
def outbox_antigo(sessao, donos):
    """Só linhas velhas, de dois donos e das cotações (sem dono): sem o piso,
    o expurgo apagaria todas."""
    a, b = donos
    c = _reservar_cursores(sessao, 5)
    sessao.add_all([
        _evento(c, a.id, 40),          # A
        _evento(c + 1, a.id, 35),      # A, a mais nova de A
        _evento(c + 2, None, 40),      # sem dono
        _evento(c + 3, None, 38),      # sem dono, a mais nova sem dono
        _evento(c + 4, b.id, 50),      # B, a única de B
    ])
    sessao.flush()
    return c


def _restantes(sessao, desde: int) -> set[int]:
    return set(sessao.scalars(
        select(PatrimonioV4Outbox.cursor).where(PatrimonioV4Outbox.cursor >= desde)
    ))


def test_expurgo_apaga_o_velho_e_guarda_a_mais_nova_de_cada_dono(sessao, outbox_antigo):
    c = outbox_antigo

    removidas = expurgar_outbox_v4(AGORA, dias=30)
    sessao.flush()

    assert removidas == 2
    assert _restantes(sessao, c) == {c + 1, c + 3, c + 4}


def test_expurgo_nao_toca_linha_mais_nova_que_a_retencao(sessao, donos):
    a, _ = donos
    c = _reservar_cursores(sessao, 2)
    sessao.add_all([_evento(c, a.id, 29), _evento(c + 1, a.id, 1)])
    sessao.flush()

    assert expurgar_outbox_v4(AGORA, dias=30) == 0
    sessao.flush()
    assert _restantes(sessao, c) == {c, c + 1}


def test_depois_do_expurgo_um_checkpoint_antigo_ainda_ve_a_mudanca(
    app_com_banco, sessao, donos, outbox_antigo
):
    """O contrato público: o consumidor pergunta só se há item depois do seu
    checkpoint. Antes e depois do expurgo, a resposta tem de ser a mesma."""
    a, _ = donos
    app_com_banco.config["PATRIMONIO_INTEGRATION_TOKEN"] = TOKEN_V4
    app_com_banco.config["PATRIMONIO_TITULAR"] = "Mariano"
    app_com_banco.config["PATRIMONIO_OWNER_ID"] = str(a.id)
    cliente = app_com_banco.test_client()
    checkpoint = patrimonio_route._cursor_v4(outbox_antigo - 1, a.id)

    def ha_mudanca() -> bool:
        resposta = cliente.get(
            "/patrimonio/v4/changes",
            query_string={"after": checkpoint, "limit": 1},
            headers={"Authorization": f"Bearer {TOKEN_V4}"},
        )
        assert resposta.status_code == 200
        return bool(resposta.get_json()["items"])

    assert ha_mudanca()
    expurgar_outbox_v4(AGORA, dias=30)
    sessao.flush()
    assert ha_mudanca()


def test_retencao_menor_que_um_dia_e_recusada(sessao):
    with pytest.raises(ValueError):
        expurgar_outbox_v4(AGORA, dias=0)
