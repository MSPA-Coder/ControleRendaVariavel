"""Compatibilidade do extrato de opções usado pela Performance."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.dialects.postgresql import dialect

from app.models import Side
from app.routes import helpers


class _SessionSpy:
    def __init__(self, option_rows):
        self.option_rows = option_rows
        self.calls = []

    def execute(self, statement):
        self.calls.append(statement)
        # position_movement_events issues stock, option and archive reads in
        # this order. The test does not need to materialize ORM rows.
        if len(self.calls) == 2:
            return iter(self.option_rows)
        return iter(())


def test_posicao_de_opcao_sem_extrato_recebe_abertura_sintetica(monkeypatch):
    spy = _SessionSpy(
        [
            (
                date(2025, 11, 7),
                42,
                901,
                Side.BUY,
                Decimal("500"),
            )
        ]
    )
    monkeypatch.setattr(helpers.db, "session", spy)

    events = helpers.position_movement_events()

    assert events == [
        helpers.HoldingEvent(
            occurred_on=date(2025, 11, 7),
            ticker_id=901,
            resulting_signed_quantity=Decimal("500"),
            position_key=("option", 42),
        )
    ]

    option_sql = str(spy.calls[1].compile(dialect=dialect()))
    assert "LEFT OUTER JOIN option_position_movements" in option_sql
    assert "coalesce" in option_sql.lower()


def test_posicao_de_opcao_com_extrato_nao_recebe_linha_sintetica(monkeypatch):
    spy = _SessionSpy(
        [
            (date(2025, 11, 7), 42, 901, Side.BUY, Decimal("500")),
            (date(2026, 1, 8), 42, 901, Side.BUY, Decimal("700")),
        ]
    )
    monkeypatch.setattr(helpers.db, "session", spy)

    events = helpers.position_movement_events()

    assert [(event.occurred_on, event.resulting_signed_quantity) for event in events] == [
        (date(2025, 11, 7), Decimal("500")),
        (date(2026, 1, 8), Decimal("700")),
    ]
    assert all(event.position_key == ("option", 42) for event in events)
