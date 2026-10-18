"""Leitura do relatório "Posição detalhada" do myProfit (XLSX ou TSV).

Mesmo cuidado do ``myprofit.py``: as duas primeiras linhas identificam o titular
(``Emitido por: <nome> - <CPF>`` e ``Período: dd/mm/aaaa até dd/mm/aaaa``); do
período só se lê a data final, que é a data da posição, e o nome e o CPF
**nunca** saem desta função. A terceira linha é o cabeçalho da tabela.

O relatório não traz corretora: a posição vem somada por ativo, e é assim que a
conciliação a compara com o CRV. Lotes do mesmo ativo (o Tesouro, por exemplo)
chegam em linhas separadas e são somados por quem consome.

O módulo é puro: recebe bytes e devolve linhas validadas.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.executions.myprofit import (
    _PERIOD,
    ASSET_GROUPS,
    B3_GROUPS,
    ExecutionReportError,
    _as_date,
    _as_decimal,
    _as_text,
    _fold,
    load_sheets,
)

REQUIRED_COLUMNS = ("Ativo", "Qtd", "Preço medio", "Total investido", "Grupo")


@dataclass(frozen=True, slots=True)
class PositionRow:
    source_row: int
    asset: str
    group_label: str
    asset_group: str | None
    quantity: Decimal
    average_price: Decimal
    total_invested: Decimal
    in_scope: bool


@dataclass(frozen=True, slots=True)
class PositionReport:
    as_of: date
    sha256: str
    rows: list[PositionRow]
    rejected_rows: list[str]


def _header_map(values: tuple[Any, ...]) -> dict[str, int] | None:
    indexes = {_fold(value): index for index, value in enumerate(values) if value is not None}
    wanted = {_fold(column): column for column in REQUIRED_COLUMNS}
    if not wanted.keys() <= indexes.keys():
        return None
    return {column: indexes[folded] for folded, column in wanted.items()}


def _parse_row(values: tuple[Any, ...], headers: dict[str, int], source_row: int) -> PositionRow:
    def cell(column: str) -> Any:
        index = headers[column]
        return values[index] if index < len(values) else None

    group_label = _as_text(cell("Grupo"), field_name="o grupo", limit=40)
    asset_group = ASSET_GROUPS.get(_fold(group_label))
    quantity = _as_decimal(cell("Qtd"), field_name="a quantidade")
    average = _as_decimal(cell("Preço medio"), field_name="o preço médio")
    invested = _as_decimal(cell("Total investido"), field_name="o total investido")
    if quantity < 0 or average < 0:
        raise ValueError("quantidade e preço médio não podem ser negativos.")
    return PositionRow(
        source_row=source_row,
        asset=_as_text(cell("Ativo"), field_name="o ativo", limit=80),
        group_label=group_label,
        asset_group=asset_group,
        quantity=quantity,
        average_price=average,
        total_invested=invested,
        in_scope=asset_group in B3_GROUPS,
    )


def read_position_report(content: bytes) -> PositionReport:
    """Lê a posição e devolve a data, as linhas válidas e as recusadas."""
    sheets = load_sheets(content)
    problem = (
        "O relatório não começa com as linhas 'Emitido por' e 'Período'; "
        "use a 'Posição detalhada' do myProfit."
    )
    for sheet_rows in sheets:
        preview = sheet_rows[:3]
        if len(preview) < 3:
            continue
        first_cell = _fold(preview[0][0] if preview[0] else None)
        period = _PERIOD.search(str(preview[1][0] if preview[1] else ""))
        if not first_cell.startswith("emitido por") or period is None:
            continue
        headers = _header_map(preview[2])
        if headers is None:
            present = {_fold(value) for value in preview[2] if value is not None}
            missing = [column for column in REQUIRED_COLUMNS if _fold(column) not in present]
            problem = "Faltam as colunas obrigatórias: " + ", ".join(missing) + "."
            continue
        try:
            as_of = _as_date(period.group(2), field_name="a data da posição")
        except ValueError as exc:
            raise ExecutionReportError("O período do relatório é inválido.") from exc
        rows: list[PositionRow] = []
        rejected: list[str] = []
        for source_row, values in enumerate(sheet_rows[3:], start=4):
            if not any(value is not None and str(value).strip() for value in values):
                continue
            if headers["Ativo"] >= len(values) or not str(values[headers["Ativo"]] or "").strip():
                continue
            try:
                rows.append(_parse_row(values, headers, source_row))
            except ValueError as exc:
                rejected.append(f"Linha {source_row}: {exc}")
        return PositionReport(
            as_of=as_of,
            sha256=hashlib.sha256(content).hexdigest(),
            rows=rows,
            rejected_rows=rejected,
        )
    raise ExecutionReportError(problem)
