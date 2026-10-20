"""Correções de 20/10/2026: preço zero, envio duplo, contrato vencido e benchmark."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from test_financial_isolation_http import client_for
from test_financial_isolation_http import review_case as review_case

from app import db
from app.models import (
    OptionContract,
    OptionExpiration,
    OptionType,
    Position,
    QuoteHistory,
    Side,
    Ticker,
)
from app.quotes.history import upsert_quote_history

pytestmark = pytest.mark.banco


def _instante(dia: date, hora: int = 12) -> datetime:
    return datetime(dia.year, dia.month, dia.day, hora, tzinfo=UTC)


def _precos(app, ticker_id: int) -> dict[date, Decimal]:
    with app.app_context():
        return {
            linha.recorded_date: linha.price
            for linha in db.session.scalars(select(QuoteHistory).where(QuoteHistory.ticker_id == ticker_id))
        }


def test_historico_nao_grava_preco_zero_nem_deixa_zero_apagar_o_fechamento(review_case):
    app, dados = review_case
    ticker_id = dados["ticker_ids"][2]
    dia = date.today() - timedelta(days=3)
    with app.app_context():
        upsert_quote_history([(ticker_id, Decimal("0"), dia, _instante(dia, 9))])
        db.session.commit()
    assert _precos(app, ticker_id) == {}

    with app.app_context():
        upsert_quote_history([(ticker_id, Decimal("21.50"), dia, _instante(dia, 17))])
        db.session.commit()
        # Leitura posterior do RTD sem negócio: não pode apagar o fechamento.
        upsert_quote_history([(ticker_id, Decimal("0"), dia, _instante(dia, 18))])
        db.session.commit()
    assert _precos(app, ticker_id) == {dia: Decimal("21.50000000")}


def test_tela_da_posicao_abre_com_fechamento_zero_no_historico(review_case):
    app, dados = review_case
    ticker_id = dados["ticker_ids"][2]
    hoje = date.today()
    with app.app_context():
        posicao = Position(
            owner_id=dados["user_ids"][2], broker_id=dados["broker"], ticker_id=ticker_id,
            portfolio_id=dados["portfolio"], quantity=Decimal("5"), average_cost=Decimal("10"),
            target_multiplier=1, side=Side.BUY, opened_on=hoje - timedelta(days=5),
        )
        db.session.add(posicao)
        # Direto no banco, como os zeros que a coleta de 21/09/2026 deixou.
        db.session.add_all(
            QuoteHistory(ticker_id=ticker_id, price=Decimal(preco), recorded_date=hoje - timedelta(days=dias),
                         recorded_at=_instante(hoje - timedelta(days=dias)))
            for dias, preco in ((3, "0"), (2, "10"), (1, "11"))
        )
        db.session.commit()
        posicao_id = posicao.id
    cliente, _ = client_for(app, dados["ids"][2])

    resposta = cliente.get(f"/positions/{posicao_id}?currency=ALL")

    assert resposta.status_code == 200


def test_envio_duplo_de_provento_nao_e_erro_de_servidor(review_case):
    app, dados = review_case
    cliente, token = client_for(app, dados["ids"][2])
    # Mesmo ativo, corretora, tipo, data e valor do provento que o caso já tem.
    formulario = {
        "csrf_token": token, "broker_id": dados["broker"], "ticker_id": dados["ticker"],
        "amount": "123.45", "payment_date": date.today().isoformat(), "kind": "dividendo",
    }

    resposta = cliente.post("/dividends", data=formulario)

    assert resposta.status_code == 409
    assert "uma renda igual" in resposta.get_data(as_text=True)  # o toast escapa o acento


def test_nova_opcao_nao_oferece_contrato_vencido(review_case):
    app, dados = review_case
    with app.app_context():
        simbolos = {t.id: t.symbol for t in db.session.scalars(select(Ticker).where(Ticker.id.in_(dados["ticker_ids"])))}
        vencido = OptionExpiration(call_code="V" + uuid4().hex[:4].upper(), put_code="W" + uuid4().hex[:4].upper(),
                                   exercise_date=date(2001, 1, 1) + timedelta(days=int(uuid4().int % 3000)))
        db.session.add(vencido)
        db.session.flush()
        contrato = OptionContract(ticker_id=dados["ticker_ids"][2], underlying_ticker_id=dados["ticker_ids"][0],
                                  expiration_id=vencido.id, option_type=OptionType.PUT, strike=Decimal("9"))
        db.session.add(contrato)
        db.session.commit()
        ids = (vencido.id, contrato.id)
    try:
        cliente, _ = client_for(app, dados["ids"][2])
        pagina = cliente.get("/options/new").get_data(as_text=True)
        assert simbolos[dados["ticker_ids"][1]] in pagina  # o contrato vigente continua
        assert simbolos[dados["ticker_ids"][2]] not in pagina  # o vencido some
    finally:
        with app.app_context():
            db.session.execute(delete(OptionContract).where(OptionContract.id == ids[1]))
            db.session.execute(delete(OptionExpiration).where(OptionExpiration.id == ids[0]))
            db.session.commit()


@pytest.mark.parametrize("caminho", ["/quotes", "/performance"])
def test_benchmark_fora_da_lista_vale_nenhum_e_nao_404(review_case, caminho):
    app, dados = review_case
    cliente, _ = client_for(app, dados["ids"][1])
    # O ticker comum do caso não é referência: nunca é candidato.
    consulta = f"{caminho}?benchmark_ticker_id={dados['ticker']}&currency=ALL"

    for cabecalhos in ({}, {"HX-Request": "true"}):
        assert cliente.get(consulta, headers=cabecalhos).status_code == 200


