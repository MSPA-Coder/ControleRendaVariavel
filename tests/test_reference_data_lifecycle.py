"""Contrato do ciclo de vida de corretoras, tickers e carteiras."""

from __future__ import annotations

from app.models import Broker, Portfolio, Ticker
from app.routes import helpers


def test_cadastros_referenciaveis_nascem_ativos() -> None:
    for model in (Broker, Ticker, Portfolio):
        column = model.__table__.columns["is_active"]
        assert column.nullable is False
        assert column.default is not None
        assert column.server_default is not None


def test_listas_de_operacao_ignoram_cadastros_arquivados(monkeypatch) -> None:
    statements = []

    monkeypatch.setattr(
        helpers.db.session, "scalars", lambda statement: (statements.append(statement) or [])
    )

    helpers.portfolio_records()
    helpers.broker_records()
    helpers.ticker_records()
    helpers.investable_ticker_records()
    helpers.benchmark_candidates()

    assert all(statement._where_criteria for statement in statements)


def test_listas_administrativas_podem_incluir_arquivados(monkeypatch) -> None:
    statements = []

    monkeypatch.setattr(
        helpers.db.session, "scalars", lambda statement: (statements.append(statement) or [])
    )

    helpers.portfolio_records(include_inactive=True)
    helpers.broker_records(include_inactive=True)
    helpers.ticker_records(include_inactive=True)

    assert all(not statement._where_criteria for statement in statements)
