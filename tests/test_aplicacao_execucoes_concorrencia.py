"""Execuções idênticas não podem ser aplicadas duas vezes por processos concorrentes."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import suppress
from datetime import date
from decimal import Decimal
from threading import Event

import pytest
from sqlalchemy import delete, select

from app import db
from app.executions import aplicacao
from app.models import Execution, ExecutionImport, Position, Side, Ticker
from app.positions.commands import PositionInput
from app.positions.use_cases import ConfirmationRequiredError, register_stock
from tests.test_financial_isolation_http import client_for
from tests.test_financial_isolation_http import review_case as review_case

pytestmark = pytest.mark.banco


@pytest.mark.parametrize("outra_operacao", ["aplicar", "linha_de_base", "aporte_manual", "edicao_manual"])
def test_operacoes_da_mesma_execucao_nao_duplicam_posicao(review_case, monkeypatch, outra_operacao):
    app, data = review_case
    owner = data["user_ids"][0]
    with app.app_context():
        ticker = db.session.get(Ticker, data["ticker_ids"][0])
        lote = ExecutionImport(owner_id=owner, file_name="sintetico.xlsx", period_start=date(2026, 6, 10),
                               period_end=date(2026, 6, 10), sha256="a" * 64,
                               row_count=1, rows_created=1, rows_updated=0)
        db.session.add(lote)
        db.session.flush()
        db.session.add(Execution(owner_id=owner, import_id=lote.id, broker_id=data["broker"],
            institution="teste", trade_date=date(2026, 6, 10), document="sintetico", line_order=1,
            content_hash="b" * 64, asset=ticker.symbol, asset_group="acao", side="C", cash_operation="D",
            quantity=Decimal("100"), price_without_fees=Decimal("10"), price_with_fees=Decimal("10"),
            fees_total=0, total_without_fees=-1000, total_with_fees=-1000, day_trade=False,
            currency="BRL", in_scope=True))
        db.session.commit()

    if outra_operacao == "edicao_manual":
        with app.app_context():
            anterior = register_stock(PositionInput(
                broker_id=data["broker"], ticker_id=data["ticker_ids"][0],
                portfolio_id=data["portfolio_ids"][0], quantity=Decimal("200"),
                average_cost=Decimal("10"), side=Side.BUY, opened_on=date(2026, 6, 1),
                target_multiplier=Decimal("1.5"),
            ), owner_id=owner, token="b" * 32).position
            position_id, version = anterior.id, anterior.edit_version
        client, csrf = client_for(app, data["ids"][0])

    leu, liberar = Event(), Event()
    verificar = aplicacao._already_entered

    def verificar_com_pausa(*args):
        resultado = verificar(*args)
        if not leu.is_set():
            leu.set()
            if not liberar.wait(10):
                raise AssertionError("a primeira aplicação não foi liberada")
        return resultado

    monkeypatch.setattr(aplicacao, "_already_entered", verificar_com_pausa)

    def aplicar():
        with app.app_context():
            resultado = aplicacao.apply_executions(owner)
            estados = [item.state for item in resultado.outcomes]
            db.session.commit()
            return estados

    def marcar():
        with app.app_context():
            quantidade = aplicacao.mark_baseline(owner, until=date(2026, 6, 10), note="conferido")
            db.session.commit()
            return [aplicacao.BASELINE] * quantidade

    def aportar():
        with app.app_context():
            try:
                register_stock(PositionInput(
                    broker_id=data["broker"], ticker_id=data["ticker_ids"][0],
                    portfolio_id=data["portfolio_ids"][0], quantity=Decimal("100"),
                    average_cost=Decimal("10"), side=Side.BUY, opened_on=date(2026, 6, 10),
                    target_multiplier=Decimal("1.5"),
                ), owner_id=owner, token="a" * 32)
            except ConfirmationRequiredError:
                return []
            return ["aporte"]

    def editar():
        resposta = client.post(f"/positions/{position_id}", data={
            "csrf_token": csrf, "edit_version": str(version), "broker_id": data["broker"],
            "ticker_id": data["ticker_ids"][0], "portfolio_id": data["portfolio_ids"][0],
            "quantity": "300", "average_cost": "10", "side": "C", "opened_on": "2026-06-01",
            "target_multiplier": "1.5",
        })
        return [] if resposta.status_code == 409 else [str(resposta.status_code)]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            primeiro = pool.submit(aplicar)
            assert leu.wait(10)
            segundo = pool.submit({"aplicar": aplicar, "linha_de_base": marcar,
                                   "aporte_manual": aportar, "edicao_manual": editar}[outra_operacao])
            try:
                with suppress(TimeoutError):
                    segundo.result(timeout=1)
            finally:
                liberar.set()
            assert sorted([primeiro.result(timeout=10), segundo.result(timeout=10)], key=len) == [[], [aplicacao.APPLIED]]
        with app.app_context():
            position = db.session.scalar(select(Position).where(Position.owner_id == owner))
            assert position.quantity == Decimal("300" if outra_operacao == "edicao_manual" else "100")
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.execute(delete(Execution).where(Execution.owner_id == owner))
            db.session.execute(delete(ExecutionImport).where(ExecutionImport.owner_id == owner))
            db.session.commit()
