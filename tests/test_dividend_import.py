from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.dividends.importer import REQUIRED_COLUMNS, DividendWorkbookError, read_dividend_workbook
from app.models import IncomeKind


def _workbook_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Proventos"
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_import_reads_headers_out_of_order_and_normalizes_refund():
    headers = [
        " DY ", "Cotas", "Ativo", "Data Com", "Recebido", "Tipo", "YOC", "Data Pgto.", "Corretora",
        "Total investido", "Total atual", "Preço Médio", "Cotação", "Valor por Cota",
    ]
    content = _workbook_bytes(
        headers,
        [[0.02, 100, "abcd3", date(2026, 1, 2), 12.3, "REEMBOLSO", 0.01, date(2026, 1, 10), "Genial", 1230, 2460, 12.3, 24.6, 0.123]],
    )

    result = read_dividend_workbook(content)

    assert result.rejected_rows == []
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.ticker == "ABCD3"
    assert row.kind is IncomeKind.DIVIDENDO
    assert row.amount == Decimal("12.3")
    assert row.com_date == date(2026, 1, 2)
    assert row.yield_on_cost == Decimal("1")
    assert row.dividend_yield == Decimal("0.5")
    assert row.amount_per_share == Decimal("0.123")


def test_import_reports_the_missing_required_columns():
    content = _workbook_bytes(list(REQUIRED_COLUMNS[:-2]), [])

    with pytest.raises(DividendWorkbookError, match="Cotação, Valor por Cota"):
        read_dividend_workbook(content)
