from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Broker, Market, Position, PositionKind, QuoteHistory, Side, Ticker

pytestmark = [pytest.mark.critical, pytest.mark.business_rule]


def _seed_ticker(symbol: str, currency: str = "BRL", is_benchmark: bool = False) -> int:
    ticker = Ticker(
        symbol=symbol,
        trading_name=symbol,
        market=Market.B3,
        rtd_market_code="B",
        currency=currency,
        is_benchmark=is_benchmark,
    )
    db.session.add(ticker)
    db.session.commit()
    return ticker.id


def _seed_broker() -> int:
    broker = Broker(name="Genial", acronym="GE")
    db.session.add(broker)
    db.session.commit()
    return broker.id


def _seed_open_position(
    ticker_id: int,
    quantity: str = "10",
    broker_id: int | None = None,
    opened_on: date = date(2026, 1, 1),
) -> None:
    db.session.add(
        Position(
            broker_id=broker_id or _seed_broker(),
            ticker_id=ticker_id,
            quantity=Decimal(quantity),
            average_cost=Decimal("10"),
            side=Side.BUY,
            opened_on=opened_on,
            quote_multiplier=Decimal("1"),
            target_multiplier=Decimal("1.5"),
            result_mode="L",
            position_kind=PositionKind.REAL,
        )
    )
    db.session.commit()


def _seed_quote_history(ticker_id: int, prices: list[tuple[str, str]]) -> None:
    for day, price in prices:
        recorded_date = date.fromisoformat(day)
        db.session.add(
            QuoteHistory(
                ticker_id=ticker_id,
                price=Decimal(price),
                recorded_date=recorded_date,
                recorded_at=datetime.combine(recorded_date, datetime.min.time(), tzinfo=UTC),
            )
        )
    db.session.commit()


def test_monthly_performance_shows_evolution_and_monthly_return(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4")
        _seed_open_position(ticker_id, quantity="10")
        _seed_quote_history(
            ticker_id,
            [
                ("2026-01-05", "100"),
                ("2026-01-31", "110"),
                ("2026-02-10", "105"),
                ("2026-02-28", "120"),
            ],
        )

    response = auth_client.get("/performance")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "01/2026" in html
    assert "02/2026" in html


def test_monthly_performance_shows_placeholder_without_open_positions(
    auth_client: FlaskClient,
) -> None:
    response = auth_client.get("/performance")

    assert response.status_code == 200
    assert "Nenhuma posição aberta encontrada para os filtros." in response.get_data(as_text=True)


def test_monthly_performance_renders_filters_with_current_defaults(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        _seed_broker()

    response = auth_client.get("/performance")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<option value="real" selected>Real</option>' in html
    assert '<option value="all" selected>Todas</option>' in html
    assert 'value="stocks"' in html
    assert '>Ações</option>' in html
    assert 'value="week"' in html
    assert 'value="semester"' in html


def test_monthly_performance_filters_by_broker(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        first_ticker_id = _seed_ticker("PETR4")
        second_ticker_id = _seed_ticker("VALE3")
        first_broker_id = _seed_broker()
        second_broker = Broker(name="Rico", acronym="RI")
        db.session.add(second_broker)
        db.session.commit()
        _seed_open_position(first_ticker_id, broker_id=first_broker_id)
        _seed_open_position(second_ticker_id, broker_id=second_broker.id)
        _seed_quote_history(first_ticker_id, [("2026-01-05", "100"), ("2026-02-05", "110")])
        _seed_quote_history(second_ticker_id, [("2026-01-05", "50"), ("2026-02-05", "55")])

    response = auth_client.get("/performance?broker=Genial&portfolio=stocks")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'value="Genial" selected' in html
    assert 'value="stocks" selected' in html
    assert "R$ 1.100,00" in html
    assert "R$ 550,00" not in html


def test_monthly_performance_period_is_selected_and_applied_by_the_backend(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4")
        _seed_open_position(ticker_id, quantity="10")
        _seed_quote_history(
            ticker_id,
            [
                ("2026-07-31", "100"),
                ("2026-08-02", "110"),
                ("2026-08-08", "120"),
            ],
        )

    all_period = auth_client.get("/performance")
    week_period = auth_client.get("/performance?period=week")

    assert "07/2026" in all_period.get_data(as_text=True)
    week_html = week_period.get_data(as_text=True)
    assert week_period.status_code == 200
    assert '<option value="week" selected>Semana</option>' in week_html
    assert "07/2026" not in week_html
    assert "R$ 1.200,00" in week_html


def test_monthly_performance_groups_by_currency(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        brl_ticker_id = _seed_ticker("PETR4", currency="BRL")
        usd_ticker_id = _seed_ticker("AAPL", currency="USD")
        broker_id = _seed_broker()
        _seed_open_position(brl_ticker_id, quantity="10", broker_id=broker_id)
        _seed_open_position(usd_ticker_id, quantity="5", broker_id=broker_id)
        _seed_quote_history(brl_ticker_id, [("2026-01-05", "100"), ("2026-02-05", "110")])
        _seed_quote_history(usd_ticker_id, [("2026-01-05", "50"), ("2026-02-05", "55")])

    response = auth_client.get("/performance")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "BRL" in html
    assert "USD" in html


@pytest.mark.security
def test_monthly_performance_requires_authentication(client: FlaskClient) -> None:
    assert client.get("/performance").status_code == 302


def test_monthly_performance_chart_truncates_to_position_start_but_table_keeps_full_history(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4")
        # Posição só foi aberta em fevereiro, mas há cotação histórica de
        # janeiro (comum quando o ticker já existia antes da compra).
        _seed_open_position(ticker_id, quantity="10", opened_on=date(2026, 2, 1))
        _seed_quote_history(
            ticker_id,
            [("2026-01-31", "100"), ("2026-02-28", "110"), ("2026-03-31", "120")],
        )
        benchmark_id = _seed_ticker("BOVA11", is_benchmark=True)
        _seed_quote_history(
            benchmark_id,
            [("2026-01-20", "50"), ("2026-02-15", "55"), ("2026-03-20", "58")],
        )

    page = auth_client.get(f"/performance?period=all&benchmark_ticker_id={benchmark_id}")

    assert page.status_code == 200
    html = page.get_data(as_text=True)

    chart_start = html.find('class="monthly-performance-chart chart-box"')
    chart_end = html.find("</div>", chart_start)
    chart_html = html[chart_start:chart_end]
    # O gráfico não deve conter o mês anterior à abertura da posição.
    assert "2026-01" not in chart_html
    assert "2026-02" in chart_html and "2026-03" in chart_html
    # Valor hipotético: R$100 (quantidade 10 × custo médio 10, mesmo custo da
    # posição real) aplicados em BOVA11 no preço de 01/02 (R$55, o primeiro
    # disponível a partir da abertura da posição) — R$100 em fevereiro (mês
    # da própria abertura, variação zero) e R$100 × 58/55 em março.
    values_start = chart_html.find("data-benchmark-values=")
    values_snippet = chart_html[values_start : values_start + 160]
    assert '"100.0000000000000000"' in values_snippet
    assert '"105.4545454545454545454545455"' in values_snippet

    table_start = html.find("<summary>Dados</summary>")
    table_html = html[table_start : html.find("</details>", table_start)]
    # A tabela "Dados" continua com o histórico completo simulado (janeiro
    # incluso), independente da comparação com o índice.
    assert "01/2026" in table_html


def test_monthly_performance_defaults_to_stock_portfolio(
    app: Flask, auth_client: FlaskClient
) -> None:
    response = auth_client.get("/performance")

    assert response.status_code == 200
    assert b'<option value="stocks" selected>' in response.data


def test_monthly_performance_offers_and_applies_benchmark_comparison(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4")
        _seed_open_position(ticker_id, quantity="10")
        _seed_quote_history(ticker_id, [("2026-01-31", "100"), ("2026-02-28", "110")])
        benchmark_id = _seed_ticker("BOVA11", is_benchmark=True)
        _seed_quote_history(benchmark_id, [("2026-01-20", "50"), ("2026-02-15", "55")])

    page = auth_client.get(f"/performance?benchmark_ticker_id={benchmark_id}")

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert f'<option value="{benchmark_id}" selected>BOVA11</option>' in html
    assert 'data-benchmark-label="BOVA11"' in html
    assert "Comparando com quanto valeria hoje se, em cada ativo, o mesmo capital hoje" in html
    # Valor hipotético: R$100 (quantidade 10 × custo médio 10) aplicados em
    # BOVA11 no preço de 01/01 (R$50, o primeiro disponível a partir da
    # abertura da posição) — R$100 em janeiro (variação zero) e R$100 × 55/50
    # = R$110 em fevereiro.
    values_start = html.find("data-benchmark-values=")
    values_snippet = html[values_start : values_start + 120]
    assert '"100.0000000000000000"' in values_snippet
    assert '"110.0000000000000000"' in values_snippet


def test_monthly_performance_hides_comparison_control_without_candidates(
    auth_client: FlaskClient,
) -> None:
    response = auth_client.get("/performance")

    assert response.status_code == 200
    assert "Comparar com" not in response.get_data(as_text=True)


def test_monthly_performance_ignores_unknown_benchmark_ticker_id(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        ticker_id = _seed_ticker("PETR4")
        _seed_open_position(ticker_id, quantity="10")
        _seed_quote_history(ticker_id, [("2026-01-31", "100")])
        _seed_ticker("BOVA11", is_benchmark=True)  # candidato existe, mas não é o id enviado

    response = auth_client.get("/performance?benchmark_ticker_id=999999")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "data-benchmark-label" not in html


def test_monthly_performance_benchmark_shadow_starts_each_asset_on_its_own_purchase_date(
    app: Flask, auth_client: FlaskClient
) -> None:
    # Este é o cenário que motivou a mudança de metodologia: PETR4 comprada
    # em janeiro, VALE3 comprada só em março (um aporte novo). A curva
    # hipotética do benchmark precisa "receber" o valor de VALE3 na mesma
    # data, e não em janeiro — senão a comparação com uma carteira que
    # recebe aportes ao longo do tempo não faz sentido (ver discussão com
    # o usuário).
    with app.app_context():
        broker_id = _seed_broker()
        petr4_id = _seed_ticker("PETR4")
        vale3_id = _seed_ticker("VALE3")
        _seed_open_position(
            petr4_id, quantity="10", broker_id=broker_id, opened_on=date(2026, 1, 1)
        )
        _seed_open_position(
            vale3_id, quantity="5", broker_id=broker_id, opened_on=date(2026, 3, 1)
        )
        _seed_quote_history(
            petr4_id,
            [
                ("2026-01-31", "100"),
                ("2026-02-28", "105"),
                ("2026-03-31", "108"),
                ("2026-04-30", "112"),
            ],
        )
        _seed_quote_history(
            vale3_id,
            [("2026-03-31", "70"), ("2026-04-30", "72")],
        )
        benchmark_id = _seed_ticker("BOVA11", is_benchmark=True)
        _seed_quote_history(
            benchmark_id,
            [
                ("2026-01-01", "100"),
                ("2026-02-01", "110"),
                ("2026-03-01", "105"),
                ("2026-04-01", "120"),
            ],
        )

    page = auth_client.get(f"/performance?period=all&benchmark_ticker_id={benchmark_id}")

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    chart_start = html.find('class="monthly-performance-chart chart-box"')
    chart_end = html.find("</div>", chart_start)
    chart_html = html[chart_start:chart_end]

    # Nenhum mês truncado: a posição mais antiga (PETR4) já é de janeiro.
    assert "2026-01" in chart_html and "2026-04" in chart_html

    values_start = chart_html.find("data-benchmark-values=")
    values_snippet = chart_html[values_start : values_start + 260]
    # Janeiro e fevereiro: só a contribuição de PETR4 (R$100 aplicados a
    # 100, valendo 100 e depois 110 — VALE3 ainda não existia).
    assert '"100.000000000000000"' in values_snippet
    assert '"110.000000000000000"' in values_snippet
    # Março em diante: as duas contribuições somadas (PETR4 R$100 a 105/100
    # + VALE3 R$50 aplicados a 105, entrando exatamente nesse preço).
    assert '"155.0000000000000000"' in values_snippet
    assert '"177.1428571428571428571428571"' in values_snippet
