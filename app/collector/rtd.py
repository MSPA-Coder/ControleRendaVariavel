from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Instrument:
    position_id: int
    ticker: str
    market_code: str
    side: str | None = None

    @property
    def topic(self) -> str:
        return f"{self.ticker}_{self.market_code}_0"

    @property
    def book_field(self) -> str | None:
        if self.market_code != "B":
            return None
        return {"C": "OCP", "V": "OVD"}.get(self.side or "")

    def effective_price_field(self, instrument_status: str) -> str:
        status = instrument_status.strip().upper()[:1]
        return self.book_field if self.book_field and status in {"A", "L"} else "ULT"


@dataclass(frozen=True, slots=True)
class QuoteValue:
    position_id: int
    last_price: Decimal
    previous_close: Decimal
    instrument_status: str
    observed_at: datetime
    last_trade_price: Decimal | None = None

    @property
    def quote_history_price(self) -> Decimal:
        """ULT for charts/history, even when an open position uses book price."""
        return self.last_trade_price if self.last_trade_price is not None else self.last_price


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
