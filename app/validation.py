from __future__ import annotations

from decimal import Decimal, InvalidOperation


def parse_finite_decimal(raw_value: str, *, field_name: str) -> Decimal:
    """Parse a user-provided decimal while rejecting NaN and infinities."""

    try:
        value = Decimal(raw_value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Informe {field_name} válido.") from exc
    if not value.is_finite():
        raise ValueError(f"Informe {field_name} finito.")
    return value
