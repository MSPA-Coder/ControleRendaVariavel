from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.collector import rtd
from app.collector.rtd import ExcelRtdQuoteProvider, Instrument


class BusyError(Exception):
    hresult = -2147418111


def test_excel_ocupado_tem_retentativa_limitada(monkeypatch):
    clock = {"now": 0.0, "calls": 0}
    monkeypatch.setattr(rtd.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rtd.time, "sleep", lambda seconds: clock.update(now=clock["now"] + seconds))

    def busy():
        clock["calls"] += 1
        raise BusyError

    with pytest.raises(TimeoutError):
        rtd._excel_call(busy, 1.0)
    assert clock["calls"] == 5
    assert clock["now"] == 1.0


def test_excel_nao_repete_erro_que_nao_seja_ocupado(monkeypatch):
    monkeypatch.setattr(rtd.time, "sleep", lambda _: pytest.fail("erro não transitório"))
    with pytest.raises(ValueError):
        rtd._excel_call(lambda: (_ for _ in ()).throw(ValueError("inválido")), float("inf"))


def test_excel_aguarda_primeiro_estado_em_vez_de_publicar_zeros(monkeypatch):
    state = {"cycles": 0, "closed": False, "quit": False}

    class Cell:
        Formula = ""

        def __init__(self, column):
            self.column = column

        def value(self):
            if state["cycles"] == 1:
                return 0
            return {1: 10, 2: 9, 3: "Aberto"}[self.column]

        Value = property(value)

    class Cells:
        def __call__(self, row, column):
            return Cell(column)

        def clear_contents(self):
            pass

        ClearContents = clear_contents

    sheet = SimpleNamespace(Cells=Cells())
    workbook = SimpleNamespace(
        Worksheets=lambda _: sheet,
        Close=lambda save: state.update(closed=not save),
    )
    excel = SimpleNamespace(
        Workbooks=SimpleNamespace(Add=lambda: workbook),
        Calculate=lambda: state.update(cycles=state["cycles"] + 1),
        Quit=lambda: state.update(quit=True),
    )
    monkeypatch.setattr(rtd.time, "sleep", lambda _: None)
    with ExcelRtdQuoteProvider(prog_id="fake", dispatch_ex=lambda _: excel) as provider:
        values = provider.fetch([Instrument(1, "ABCD3", "B")])
    assert values[0].last_price == 10
    assert values[0].previous_close == 9
    assert state == {"cycles": 2, "closed": True, "quit": True}
