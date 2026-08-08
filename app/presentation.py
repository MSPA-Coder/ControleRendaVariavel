from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from flask import Flask

from app.domain import MARKET_TIMEZONE

COLLECTOR_STATUS_LABELS = {
    "online": "Coletor online",
    "stale": "Coletor atrasado",
    "error": "Coletor com erro",
    "waiting": "Coletor aguardando leitura",
}

RTD_STATUS_LABELS = {
    "waiting_for_profit": "RTD aguardando Profit",
    "starting": "RTD iniciando",
    "backoff": "RTD em nova tentativa",
    "error": "RTD com erro",
    "unavailable": "RTD indisponível",
}


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

    @app.template_filter("read_at")
    def read_at(value: str | None) -> str:
        """Instante ISO de uma leitura do coletor, no fuso do mercado.

        A formatação passou do navegador para o servidor quando o indicador
        virou um fragmento HTMX: o cliente só recebe texto pronto.
        """
        if not value:
            return "Sem leitura registrada"
        return datetime.fromisoformat(value).astimezone(MARKET_TIMEZONE).strftime(
            "%d/%m/%Y %H:%M:%S"
        )

    @app.template_filter("collector_status_label")
    def collector_status_label(status: str | None) -> str:
        return COLLECTOR_STATUS_LABELS.get(status or "", "Coletor indisponível")

    @app.template_filter("rtd_status_label")
    def rtd_status_label(status: str | None, running: bool) -> str:
        """Rótulo do controle do coletor RTD.

        Estados intermediários (aguardando o Profit, iniciando, em nova
        tentativa) têm texto próprio; fora deles, o rótulo apenas reflete se
        o coletor está ligado.
        """
        return RTD_STATUS_LABELS.get(
            status or "", "RTD ligado" if running else "RTD desligado"
        )

    @app.template_filter("sign_class")
    def sign_class(value: Decimal | None) -> str:
        if value is None or value == 0:
            return ""
        return "negative" if value < 0 else "positive"
