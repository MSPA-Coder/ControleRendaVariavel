from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Instrument:
    position_id: int
    ticker: str
    market_code: str

    @property
    def topic(self) -> str:
        return f"{self.ticker}_{self.market_code}_0"


@dataclass(frozen=True, slots=True)
class QuoteValue:
    position_id: int
    last_price: Decimal
    previous_close: Decimal
    instrument_status: str
    observed_at: datetime


class QuoteProvider(Protocol):
    def fetch(self, instruments: list[Instrument]) -> list[QuoteValue]: ...


def parse_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("valor RTD ausente ou inválido")
    normalized = str(value).strip().replace("\xa0", "")
    if not normalized:
        raise ValueError("valor RTD vazio")
    if "," in normalized and "." in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")
    try:
        result = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"valor RTD não numérico: {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("valor RTD fora do domínio")
    return result


class ExcelRtdQuoteProvider:
    """Keeps one private Excel/RTD session alive until explicitly closed."""

    def __init__(
        self,
        *,
        prog_id: str,
        timeout_seconds: float = 10,
        refresh_seconds: float = 2,
        visible: bool = False,
        dispatch_ex: Callable[[str], Any] | None = None,
        com_initialize: Callable[[], None] | None = None,
        com_uninitialize: Callable[[], None] | None = None,
    ) -> None:
        self.prog_id = prog_id
        self.timeout_seconds = timeout_seconds
        self.refresh_seconds = refresh_seconds
        self.visible = visible
        self._dispatch_ex = dispatch_ex
        self._com_initialize = com_initialize
        self._com_uninitialize = com_uninitialize
        self._excel: Any | None = None
        self._workbook: Any | None = None
        self._sheet: Any | None = None
        self._instrument_signature: tuple[tuple[int, str], ...] = ()

    def __enter__(self) -> ExcelRtdQuoteProvider:
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def open(self) -> None:
        if self._excel is not None:
            return
        if self._dispatch_ex is None:
            try:
                import pythoncom
                import win32com.client
            except ImportError as exc:
                raise RuntimeError(
                    "instale o extra 'rtd' e execute este comando no Windows"
                ) from exc
            self._dispatch_ex = win32com.client.DispatchEx
            self._com_initialize = pythoncom.CoInitialize
            self._com_uninitialize = pythoncom.CoUninitialize

        if self._com_initialize is not None:
            self._com_initialize()
        try:
            self._excel = self._dispatch_ex("Excel.Application")
            self._excel.Visible = self.visible
            self._excel.DisplayAlerts = False
            self._workbook = self._excel.Workbooks.Add()
            self._sheet = self._workbook.Worksheets(1)
        except Exception:
            if self._com_uninitialize is not None:
                self._com_uninitialize()
            self._excel = None
            raise

    def close(self) -> None:
        workbook, excel = self._workbook, self._excel
        self._sheet = None
        self._workbook = None
        self._excel = None
        self._instrument_signature = ()
        try:
            if workbook is not None:
                workbook.Close(False)
        finally:
            try:
                if excel is not None:
                    excel.Quit()
            finally:
                if self._com_uninitialize is not None:
                    self._com_uninitialize()

    def _sync_instruments(self, instruments: list[Instrument]) -> None:
        signature = tuple((item.position_id, item.topic) for item in instruments)
        if signature == self._instrument_signature:
            return
        if self._sheet is None:
            raise RuntimeError("sessão Excel RTD não inicializada")
        self._sheet.Cells.ClearContents()
        for row, instrument in enumerate(instruments, start=1):
            topic = instrument.topic.replace('"', '""')
            prog_id = self.prog_id.replace('"', '""')
            self._sheet.Cells(row, 1).Formula = f'=RTD("{prog_id}",,"{topic}","ULT")'
            self._sheet.Cells(row, 2).Formula = f'=RTD("{prog_id}",,"{topic}","FEC")'
            self._sheet.Cells(row, 3).Formula = f'=RTD("{prog_id}",,"{topic}","EST")'
        self._instrument_signature = signature

    def fetch(self, instruments: list[Instrument]) -> list[QuoteValue]:
        if not instruments:
            return []
        self.open()
        self._sync_instruments(instruments)
        if self._excel is None or self._sheet is None:
            raise RuntimeError("sessão Excel RTD não inicializada")

        deadline = time.monotonic() + self.timeout_seconds
        values: list[QuoteValue] = []
        while time.monotonic() < deadline:
            self._excel.Calculate()
            values.clear()
            try:
                for row, instrument in enumerate(instruments, start=1):
                    values.append(
                        QuoteValue(
                            position_id=instrument.position_id,
                            last_price=parse_decimal(self._sheet.Cells(row, 1).Value),
                            previous_close=parse_decimal(self._sheet.Cells(row, 2).Value),
                            instrument_status=str(self._sheet.Cells(row, 3).Value or "")[:16],
                            observed_at=datetime.now(UTC),
                        )
                    )
            except ValueError:
                time.sleep(min(self.refresh_seconds, 0.25))
                continue
            return list(values)
        raise TimeoutError(f"RTD não respondeu em {self.timeout_seconds:g}s")
