from datetime import date
from types import SimpleNamespace

from app.models import Market
from app.routes import helpers


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _row(ticker_id, symbol, start_date, *, is_benchmark=False):
    return SimpleNamespace(
        ticker_id=ticker_id,
        symbol=symbol,
        market=Market.B3,
        is_benchmark=is_benchmark,
        start_date=start_date,
    )


def _benchmark_row(ticker_id, symbol):
    return SimpleNamespace(id=ticker_id, symbol=symbol, market=Market.B3)


def test_quote_update_targets_inclui_opcoes_e_usa_a_menor_data(monkeypatch):
    executions = iter(
        [
            _Rows([_row(1, "PETR4", date(2026, 2, 10))]),
            _Rows([_row(1, "PETR4", date(2026, 1, 15)), _row(2, "VALE3", date(2026, 3, 1))]),
            _Rows([_benchmark_row(3, "BOVA11")]),
        ]
    )
    monkeypatch.setattr(helpers.db.session, "execute", lambda _statement: next(executions))

    targets = helpers.quote_update_targets()

    assert [(target.symbol, start) for target, start in targets] == [
        ("BOVA11", date(2026, 1, 15)),
        ("PETR4", date(2026, 1, 15)),
        ("VALE3", date(2026, 3, 1)),
    ]


def test_quote_update_targets_sem_posicoes_usa_lookback_do_benchmark(monkeypatch):
    executions = iter([_Rows([]), _Rows([]), _Rows([_benchmark_row(3, "BOVA11")])])
    monkeypatch.setattr(helpers.db.session, "execute", lambda _statement: next(executions))

    targets = helpers.quote_update_targets()

    assert len(targets) == 1
    assert targets[0][0].symbol == "BOVA11"
    assert targets[0][1] < date.today()
