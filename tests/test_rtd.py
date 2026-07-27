from decimal import Decimal

import pytest

from app.rtd import ExcelRtdQuoteProvider, Instrument, parse_decimal


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234,56", Decimal("1234.56")),
        ("1234,56", Decimal("1234.56")),
        (12.5, Decimal("12.5")),
    ],
)
def test_parse_decimal_accepts_rtd_formats(raw: object, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", [None, "", True, "-1", "erro"])
def test_parse_decimal_rejects_bad_quotes(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_decimal(raw)


def test_instrument_builds_same_topic_as_sheet() -> None:
    assert Instrument(1, "BBAS3", "B").topic == "BBAS3_B_0"


class FakeCell:
    def __init__(self, value: object = None) -> None:
        self.Value = value
        self.Formula = ""


class FakeCells:
    def __init__(self) -> None:
        self.items: dict[tuple[int, int], FakeCell] = {}
        self.clear_count = 0

    def __call__(self, row: int, column: int) -> FakeCell:
        defaults: dict[int, object] = {1: "20.50", 2: "20.00", 3: "Aberto"}
        return self.items.setdefault((row, column), FakeCell(defaults[column]))

    def ClearContents(self) -> None:
        self.clear_count += 1


class FakeSheet:
    def __init__(self) -> None:
        self.Cells = FakeCells()


class FakeWorkbook:
    def __init__(self, sheet: FakeSheet) -> None:
        self.sheet = sheet
        self.close_count = 0

    def Worksheets(self, _index: int) -> FakeSheet:
        return self.sheet

    def Close(self, _save: bool) -> None:
        self.close_count += 1


class FakeWorkbooks:
    def __init__(self, workbook: FakeWorkbook) -> None:
        self.workbook = workbook
        self.add_count = 0

    def Add(self) -> FakeWorkbook:
        self.add_count += 1
        return self.workbook


class FakeExcel:
    def __init__(self) -> None:
        self.sheet = FakeSheet()
        self.workbook = FakeWorkbook(self.sheet)
        self.Workbooks = FakeWorkbooks(self.workbook)
        self.Visible = False
        self.DisplayAlerts = False
        self.calculate_count = 0
        self.quit_count = 0

    def Calculate(self) -> None:
        self.calculate_count += 1

    def Quit(self) -> None:
        self.quit_count += 1


def test_excel_provider_reuses_one_excel_session_and_formula_set() -> None:
    excel = FakeExcel()
    dispatch_count = 0

    def dispatch(_prog_id: str) -> FakeExcel:
        nonlocal dispatch_count
        dispatch_count += 1
        return excel

    provider = ExcelRtdQuoteProvider(prog_id="server", dispatch_ex=dispatch)
    instruments = [Instrument(1, "BBAS3", "B")]

    with provider:
        assert provider.fetch(instruments)[0].last_price == Decimal("20.50")
        assert provider.fetch(instruments)[0].previous_close == Decimal("20.00")

    assert dispatch_count == 1
    assert excel.Workbooks.add_count == 1
    assert excel.sheet.Cells.clear_count == 1
    assert excel.calculate_count == 2
    assert excel.workbook.close_count == 1
    assert excel.quit_count == 1
