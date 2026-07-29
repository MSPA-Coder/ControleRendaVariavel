from __future__ import annotations

from decimal import Decimal

from flask import Flask


def _number(value: Decimal, decimals: int) -> str:
    return f"{value:,.{decimals}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def register_filters(app: Flask) -> None:
    @app.template_filter("money")
    def money(value: Decimal | None) -> str:
        return "-" if value is None else f"R$ {_number(value, 2)}"

    @app.template_filter("currency")
    def currency(value: Decimal | None, code: str, decimals: int = 2) -> str:
        if value is None:
            return "-"
        prefix = "R$" if code == "BRL" else "US$"
        return f"{prefix} {_number(value, decimals)}"

    @app.template_filter("quantity")
    def quantity(value: Decimal) -> str:
        exponent = value.as_tuple().exponent
        decimals = max(0, -exponent) if isinstance(exponent, int) else 0
        return _number(value, min(decimals, 8))

    @app.template_filter("number")
    def number(value: Decimal | None, decimals: int = 2) -> str:
        return "-" if value is None else _number(value, decimals)

    @app.template_filter("percent")
    def percent(value: Decimal | None, decimals: int = 1) -> str:
        if value is None:
            return "-"
        return f"{_number(value * 100, decimals)}%"

    @app.template_filter("sign_class")
    def sign_class(value: Decimal | None) -> str:
        if value is None or value == 0:
            return ""
        return "negative" if value < 0 else "positive"
