from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.routes import quotes


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def test_linhas_de_aportes_separam_abertura_aumentos_e_opcoes(monkeypatch):
    executions = iter(
        [
            _Rows(
                [
                    SimpleNamespace(
                        occurred_on=date(2026, 1, 10),
                        price=Decimal("12.34"),
                        kind=quotes.PositionMovementKind.OPEN,
                    ),
                    SimpleNamespace(
                        occurred_on=date(2026, 2, 12),
                        price=Decimal("15.67"),
                        kind=quotes.PositionMovementKind.INCREASE,
                    ),
                ]
            ),
            _Rows(
                [
                    SimpleNamespace(
                        occurred_on=date(2026, 2, 5),
                        price=Decimal("2.50"),
                        kind=quotes.PositionMovementKind.OPEN,
                    ),
                ]
            ),
        ]
    )
    monkeypatch.setattr(quotes, "current_owner_id", lambda: 17)
    monkeypatch.setattr(quotes.db.session, "execute", lambda _statement: next(executions))

    assert quotes.open_position_entry_lines(9) == [
        {
            "openedOn": "2026-01-10",
            "entryPrice": "12.34",
            "label": "Abertura · ação em 10/01/2026",
        },
        {
            "openedOn": "2026-02-05",
            "entryPrice": "2.50",
            "label": "Abertura · opção em 05/02/2026",
        },
        {
            "openedOn": "2026-02-12",
            "entryPrice": "15.67",
            "label": "Aumento · ação em 12/02/2026",
        },
    ]
