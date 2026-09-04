from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.routes.quotes import common_quote_start_date, trim_to_common_quote_start


def _quote(day: date, price: str) -> SimpleNamespace:
    return SimpleNamespace(recorded_date=day, price=price)


def test_comparacao_comeca_na_primeira_data_exata_em_comum():
    history = [
        _quote(date(2026, 1, 2), "10"),
        _quote(date(2026, 1, 5), "11"),
        _quote(date(2026, 1, 6), "12"),
    ]
    benchmark = [
        _quote(date(2026, 1, 3), "20"),
        _quote(date(2026, 1, 5), "21"),
        _quote(date(2026, 1, 7), "22"),
    ]

    trimmed_history, trimmed_benchmark, start_date = trim_to_common_quote_start(
        history, benchmark
    )

    assert start_date == date(2026, 1, 5)
    assert [entry.recorded_date for entry in trimmed_history] == [
        date(2026, 1, 5),
        date(2026, 1, 6),
    ]
    assert [entry.recorded_date for entry in trimmed_benchmark] == [
        date(2026, 1, 5),
        date(2026, 1, 7),
    ]
    assert [entry.recorded_date for entry in history] == [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
    ]


def test_comparacao_sem_data_comum_retorna_series_vazias_e_estado_indisponivel():
    history = [_quote(date(2026, 1, 2), "10")]
    benchmark = [_quote(date(2026, 1, 3), "20")]

    assert common_quote_start_date(history, benchmark) is None
    assert trim_to_common_quote_start(history, benchmark) == ([], [], None)
