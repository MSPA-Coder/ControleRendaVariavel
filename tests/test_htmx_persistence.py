from __future__ import annotations

from pathlib import Path

import pytest

from app.routes import dividends, options, positions, quotes, tables, transactions

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "template",
    [
        "position_form.html",
        "option_form.html",
        "dividend_form.html",
        "transaction_form.html",
        "close_position_form.html",
        "close_option_form.html",
        "table_brokers.html",
        "table_tickers.html",
        "table_contracts.html",
        "table_expirations.html",
        "partials/transactions_results.html",
    ],
)
def test_operacoes_destrutivas_nao_usam_htmx_boost_sem_alvo(template: str):
    conteudo = (ROOT / "app" / "templates" / template).read_text(encoding="utf-8")

    assert 'hx-boost="true"' not in conteudo
    assert "data-sa-confirmar" in conteudo


def test_botoes_de_persistencia_tem_confirmacao_visual():
    templates = [
        "users.html",
        "partials/users_results.html",
        "partials/portfolios_results.html",
        "partials/quotes_results.html",
        "settings.html",
        "table_brokers.html",
        "table_tickers.html",
        "table_contracts.html",
        "table_expirations.html",
        "position_form.html",
        "option_form.html",
        "dividend_form.html",
        "transaction_form.html",
        "close_position_form.html",
        "close_option_form.html",
    ]

    for template in templates:
        conteudo = (ROOT / "app" / "templates" / template).read_text(encoding="utf-8")
        assert "data-sa-confirmar" in conteudo or "hx-confirm" in conteudo, template


def test_update_position_retorna_formulario_em_modo_edicao_apos_erro(monkeypatch, app):
    class Position:
        movements = [object(), object()]

    capturado: dict[str, object] = {}

    monkeypatch.setattr(positions.db, "get_or_404", lambda *_args: Position())
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
        movements = [object()]

    capturado: dict[str, object] = {}

    monkeypatch.setattr(options.db, "get_or_404", lambda *_args: Position())
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

    monkeypatch.setattr(transactions.db, "get_or_404", lambda *_args: Transaction())
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


def test_confirmacao_de_carteira_simulada_falha_fechada():
    script = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert "showConfirmationUnavailable(form)" in script
    assert "event.preventDefault();" in script
    assert "A operação não foi enviada" in script


def test_acoes_se_atualiza_no_proximo_ciclo_sem_polling_continuo():
    template = (ROOT / "app" / "templates" / "partials" / "portfolio_results.html").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert "data-quote-refresh-schedule" in template
    assert 'hx-trigger="quote-refresh-due"' in template
    assert "every {{ poll_interval_seconds }}s" not in template
    assert "schedulePortfolioRefresh" in script
    assert "QUOTE_REFRESH_GRACE_MS" in script


def test_resposta_htmx_antiga_nao_sobrescreve_intencao_mais_recente_em_acoes():
    script = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert "portfolioRequestGenerations = new WeakMap()" in script
    assert "portfolioExpansionRequests = new Set()" in script
    assert "latestPortfolioRequestGeneration += 1" in script
    assert 'requester.matches(".row-toggle")' in script
    assert "isScheduledRefresh && portfolioExpansionRequests.size > 0" in script
    assert 'event.preventDefault();\n      return;' in script
    assert 'document.addEventListener("htmx:afterRequest"' in script
    assert 'document.addEventListener("htmx:beforeSwap"' in script
    assert "generation === latestPortfolioRequestGeneration" in script
    assert "event.preventDefault();" in script


def test_grafico_de_vencimentos_reinicializa_apos_swap_htmx():
    script = (ROOT / "app" / "static" / "expiration-chart.js").read_text(encoding="utf-8")

    assert 'document.addEventListener("htmx:afterSwap", renderExpirationChart)' in script
    assert 'container.dataset.chartInitialized === "true"' in script


def test_scripts_de_graficos_sao_carregados_antes_do_primeiro_resultado():
    templates = {
        "quotes.html": ("quote-history-chart.js", "selected_ticker and history"),
        "performance.html": ("monthly-performance-chart.js", "reports"),
        "exposure_asset.html": ("chart_scripts()", "allocation_charts"),
        "exposure_broker.html": ("chart_scripts()", "allocation_charts"),
        "exposure_market.html": ("chart_scripts()", "allocation_charts"),
    }

    for name, (script_name, removed_guard) in templates.items():
        source = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
        assert script_name in source
        assert removed_guard not in source
