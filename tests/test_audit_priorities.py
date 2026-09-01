from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from flask import render_template

from app.models import Broker, OptionContract, Portfolio, Ticker
from app.routes import dividends, helpers, options, quotes, transactions


def _reference(model, identifier):
    if model is Ticker:
        return SimpleNamespace(id=identifier, is_benchmark=False)
    if model in {Broker, Portfolio}:
        return SimpleNamespace(id=identifier)
    raise AssertionError(f"consulta inesperada: {model}")


def test_closed_transaction_rejects_future_dates(app, monkeypatch):
    future = (date.today() + timedelta(days=1)).isoformat()
    monkeypatch.setattr(transactions.db.session, "get", _reference)
    form = {
        "broker_id": "1",
        "ticker_id": "2",
        "quantity": "1",
        "average_cost": "10",
        "exit_price": "11",
        "opened_on": future,
        "closed_on": future,
        "side": "C",
        "result_mode": "L",
        "portfolio_id": "3",
    }
    with (
        app.test_request_context("/transactions", method="POST", data=form),
        pytest.raises(ValueError, match="não podem estar no futuro"),
    ):
        transactions._parse_form()


def test_received_dividend_rejects_future_payment(app, monkeypatch):
    monkeypatch.setattr(dividends.db.session, "get", _reference)
    form = {
        "broker_id": "1",
        "ticker_id": "2",
        "amount": "10",
        "payment_date": (date.today() + timedelta(days=1)).isoformat(),
        "kind": "dividendo",
    }
    with (
        app.test_request_context("/dividends", method="POST", data=form),
        pytest.raises(ValueError, match="não pode estar no futuro"),
    ):
        dividends._parse_form()


def test_new_option_position_rejects_expired_contract(app, monkeypatch):
    expired_contract = SimpleNamespace(
        id=2,
        expiration=SimpleNamespace(exercise_date=date.today() - timedelta(days=1)),
    )

    def get(model, identifier):
        if model is OptionContract:
            return expired_contract
        return _reference(model, identifier)

    monkeypatch.setattr(options.db.session, "get", get)
    form = {
        "broker_id": "1",
        "contract_id": "2",
        "quantity": "1",
        "average_cost": "1",
        "target_price": "",
        "side": "C",
        "opened_on": date.today().isoformat(),
        "result_mode": "L",
        "portfolio_id": "3",
    }
    with app.test_request_context("/options/positions", method="POST", data=form):
        with pytest.raises(ValueError, match="contrato já vencido"):
            options._parse_position()
        parsed = options._parse_position(permitir_contrato_vencido=True)
    assert parsed.contract_id == 2


def test_manual_quote_rejects_zero_before_touching_database(app, monkeypatch):
    monkeypatch.setattr(
        quotes.db.session,
        "get",
        lambda *_args: (_ for _ in ()).throw(AssertionError("não deve consultar")),
    )
    monkeypatch.setattr(quotes, "_quote_management_response", lambda ticker_id: str(ticker_id))
    form = {
        "ticker_id": "2",
        "recorded_date": date.today().isoformat(),
        "price": "0",
    }
    with app.test_request_context("/quotes", method="POST", data=form):
        response = quotes.create_quote_history_entry()
    assert response == "2"


def test_missing_quotes_are_explicit_and_zero_groups_are_omitted(app):
    position = SimpleNamespace(
        ticker="ZZTESTE4",
        broker="ZZTESTE Corretora",
        market=SimpleNamespace(value="B3"),
        portfolio_ref=SimpleNamespace(name="ZZTESTE Real"),
        currency="BRL",
    )
    missing_view = SimpleNamespace(position=position, metrics=None)
    missing_group = SimpleNamespace(
        positions=[missing_view],
        broker=position.broker,
        currency="BRL",
        current_total=0,
        current_weight=None,
    )

    rows = helpers.missing_quote_rows([missing_view])
    assert rows == [
        {
            "ticker": "ZZTESTE4",
            "broker": "ZZTESTE Corretora",
            "market": "B3",
            "portfolio": "ZZTESTE Real",
            "currency": "BRL",
        }
    ]
    assert helpers.exposure_group_rows([missing_group], lambda group: group.broker) == []

    with app.test_request_context("/analysis/exposure-asset"):
        html = render_template(
            "partials/exposure_results.html",
            group_rows=[],
            group_heading="",
            allocation_charts=[],
            converted_chart=None,
            heading="Alocação por ativo",
            subject="ativo",
            missing_quote_rows=rows,
        )
    assert "ZZTESTE4" in html
    assert "Aguardando primeira cotação RTD" in html
    assert "Nenhuma posição encontrada" not in html
