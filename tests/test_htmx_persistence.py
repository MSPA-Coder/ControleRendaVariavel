from __future__ import annotations

from pathlib import Path

from app.routes import dividends, options, positions, quotes, tables, transactions

ROOT = Path(__file__).parents[1]


def test_update_position_retorna_formulario_em_modo_edicao_apos_erro(monkeypatch, app):
    class Position:
        ticker_id = 1
        movements = [object(), object()]

    capturado: dict[str, object] = {}

    monkeypatch.setattr(positions, "owned_or_404_for_update", lambda *_args: Position())
    monkeypatch.setattr(
        positions,
        "_parse_form",
        lambda: (_ for _ in ()).throw(ValueError("dados inválidos")),
    )
    monkeypatch.setattr(
        positions,
        "render_template",
        lambda _template, **context: capturado.update(context) or context,
    )
    monkeypatch.setattr(positions, "broker_records", lambda: [])
    monkeypatch.setattr(positions, "investable_ticker_records", lambda: [])
    monkeypatch.setattr(positions, "portfolio_records", lambda: [])

    with app.test_request_context("/positions/42", method="POST", data={}):
        resposta = positions.update_position(42)

    assert resposta[1] == 422
    assert capturado["edit_mode"] is True
    assert capturado["position_id"] == 42
    assert capturado["movement_count"] == 2


def test_update_option_position_retorna_formulario_em_modo_edicao_apos_erro(monkeypatch, app):
    class Position:
        contract_id = 1
        movements = [object()]

    capturado: dict[str, object] = {}

    monkeypatch.setattr(options, "owned_or_404_for_update", lambda *_args: Position())
    monkeypatch.setattr(
        options,
        "_parse_position",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("dados inválidos")),
    )
    monkeypatch.setattr(
        options,
        "render_template",
        lambda _template, **context: capturado.update(context) or context,
    )
    monkeypatch.setattr(options, "_brokers", lambda: [])
    monkeypatch.setattr(options, "_contracts", lambda: [])
    monkeypatch.setattr(options, "portfolio_records", lambda: [])

    with app.test_request_context("/options/positions/42", method="POST", data={}):
        resposta = options.update_position(42)

    assert resposta[1] == 422
    assert capturado["edit_mode"] is True
    assert capturado["position_id"] == 42
    assert capturado["movement_count"] == 1


def test_update_dividend_retorna_formulario_em_modo_edicao_apos_erro(monkeypatch, app):
    capturado: dict[str, object] = {}

    monkeypatch.setattr(dividends.db, "get_or_404", lambda *_args: object())
    monkeypatch.setattr(
        dividends,
        "_parse_form",
        lambda: (_ for _ in ()).throw(ValueError("dados inválidos")),
    )
    monkeypatch.setattr(
        dividends,
        "render_template",
        lambda _template, **context: capturado.update(context) or context,
    )
    monkeypatch.setattr(dividends, "broker_records", lambda: [])
    monkeypatch.setattr(dividends, "investable_ticker_records", lambda: [])

    with app.test_request_context("/dividends/42", method="POST", data={}):
        resposta = dividends.update_dividend(42)

    assert resposta[1] == 422
    assert capturado["edit_mode"] is True
    assert capturado["dividend_id"] == 42


def test_update_transaction_retorna_formulario_em_modo_edicao_apos_erro(monkeypatch, app):
    class Transaction:
        status = transactions.TransactionStatus.CLOSED
        option_contract_id = None
        source_position_id = None

    capturado: dict[str, object] = {}

    monkeypatch.setattr(transactions, "owned_or_404_for_update", lambda *_args: Transaction())
    monkeypatch.setattr(
        transactions,
        "_parse_form",
        lambda: (_ for _ in ()).throw(ValueError("dados inválidos")),
    )
    monkeypatch.setattr(
        transactions,
        "render_template",
        lambda _template, **context: capturado.update(context) or context,
    )
    monkeypatch.setattr(transactions, "broker_records", lambda: [])
    monkeypatch.setattr(transactions, "investable_ticker_records", lambda: [])
    monkeypatch.setattr(transactions, "portfolio_records", lambda: [])

    with app.test_request_context("/transactions/42", method="POST", data={}):
        resposta = transactions.update_transaction(42)

    assert resposta[1] == 422
    assert capturado["edit_mode"] is True
    assert capturado["transaction_id"] == 42


def test_resposta_de_cotacoes_preserva_ticker_e_benchmark(monkeypatch, app):
    capturado: dict[str, object] = {}

    monkeypatch.setattr(quotes, "_quote_history_context", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        quotes,
        "render_template",
        lambda _template, **context: capturado.update(context) or context,
    )

    with app.test_request_context(
        "/quotes",
        method="POST",
        headers={"HX-Request": "true"},
        data={"ticker_id": "4", "benchmark_ticker_id": "9"},
    ):
        quotes._quote_management_response(None)

    assert capturado["ticker_id"] == 4
    assert capturado["benchmark_id"] == 9
    assert capturado["management_open"] is True


def test_resposta_de_carteiras_preserva_painel_aberto(monkeypatch, app):
    capturado: dict[str, object] = {}

    monkeypatch.setattr(tables, "_portfolios_results_context", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        tables,
        "render_template",
        lambda _template, **context: capturado.update(context) or context,
    )

    with app.test_request_context(
        "/tables/portfolios/4",
        method="POST",
        headers={"HX-Request": "true"},
        data={"portfolios_management_open": "1"},
    ):
        tables._portfolios_response(4)

    assert capturado["selected_portfolio_id"] == 4
    assert capturado["management_open"] is True
