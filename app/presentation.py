from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from flask import Flask
from sharedauth.formatting import numero

from app.domain import MARKET_TIMEZONE
from app.instrument_status import (
    INSTRUMENT_STATUS_DESCRIPTIONS,
    INSTRUMENT_STATUS_LABELS,
)
from app.privacy import mask_text, mask_value

COLLECTOR_STATUS_LABELS = {
    "online": "Coletor online",
    "stale": "Coletor atrasado",
    "error": "Coletor com erro",
    "waiting": "Coletor aguardando leitura",
}

POSITION_MOVEMENT_LABELS = {
    "open": "Abertura",
    "increase": "Aumento",
    "decrease": "Encerramento parcial",
    "adjustment": "Ajuste",
}

RTD_STATUS_LABELS = {
    "waiting_for_profit": "RTD aguardando Profit",
    "starting": "RTD iniciando",
    "backoff": "RTD em nova tentativa",
    "error": "RTD com erro",
    "unavailable": "RTD indisponível",
}

INCOME_KIND_LABELS = {
    "dividendo": "Dividendo",
    "jcp": "JCP",
    "aluguel": "Aluguel de ações",
}
"""Rótulos das rendas (``app.models.IncomeKind``). Mantidos aqui, e não
derivados do valor gravado, porque "JCP" é sigla e "aluguel" sozinho seria
ambíguo numa tela de investimentos."""


def _number(value: Decimal, decimals: int, trim: bool = False) -> str:
    """Formata no padrão brasileiro (milhar com ponto, decimal com vírgula).

    A conta mora em ``sharedauth.formatting``: esta rotina era idêntica,
    caractere por caractere, à do ControleBancario — as duas tinham sido
    escritas separadamente e coincidiram até no truque de usar ``\\x00`` como
    marcador para trocar os separadores sem passar duas vezes pelo mesmo
    caractere.

    ``trim`` omite a parte decimal quando ela é inteiramente zero. É o que as
    telas de Ações e Opções usam: um ",00" repetido em cada coluna de dinheiro
    só consome largura em uma tabela que já é larga demais. As telas onde o
    alinhamento de casas decimais importa mais que a largura continuam sem
    ele.
    """
    return numero(value, casas=decimals, remover_decimal_zero=trim)


def register_filters(app: Flask) -> None:
    @app.template_filter("privacy_text")
    def privacy_text(value: str) -> str:
        return mask_text(value)

    @app.template_filter("money")
    def money(value: Decimal | None) -> str:
        return "-" if value is None else mask_value(f"R$ {_number(value, 2)}")

    @app.template_filter("currency")
    def currency(
        value: Decimal | None, code: str, decimals: int = 2, trim: bool = False
    ) -> str:
        if value is None:
            return "-"
        prefix = "R$" if code == "BRL" else "US$"
        return mask_value(f"{prefix} {_number(value, decimals, trim)}")

    @app.template_filter("currency_symbol")
    def currency_symbol(code: str) -> str:
        """Símbolo da moeda, para quando ele é exibido uma vez no título de um
        card em vez de repetido em cada valor — ver os cards de totais da
        Carteira, onde o prefixo por valor comia a largura e quebrava linha."""
        return "R$" if code == "BRL" else "US$"

    @app.template_filter("quantity")
    def quantity(value: Decimal, currency: str | None = None) -> str:
        """Quantidade de uma posição ou de um movimento.

        Com a moeda informada vale a regra das telas de carteira: papel em BRL
        é negociado em lote inteiro, então casa decimal ali só polui; fora do
        Brasil a fração existe, e quatro casas cobrem o que as corretoras
        informam. Nas duas, ``trim`` apaga a parte decimal quando ela é toda
        zero. Sem a moeda — as telas que listam quantidades de várias posições
        juntas — o formato preserva as casas que o próprio ``Decimal`` carrega.
        """
        if currency is not None:
            return mask_value(_number(value, 0 if currency == "BRL" else 4, trim=True))
        exponent = value.as_tuple().exponent
        decimals = max(0, -exponent) if isinstance(exponent, int) else 0
        return mask_value(_number(value, min(decimals, 8)))

    @app.template_filter("number")
    def number(value: Decimal | None, decimals: int = 2, trim: bool = False) -> str:
        return "-" if value is None else mask_value(_number(value, decimals, trim))

    @app.template_filter("percent")
    def percent(value: Decimal | None, decimals: int = 1) -> str:
        if value is None:
            return "-"
        return mask_value(f"{_number(value * 100, decimals)}%")

    @app.template_filter("instrument_status_label")
    def instrument_status_label(status: str | None) -> str:
        """Nome curto do estado de negociação, o que a coluna ST exibe.

        A letra sozinha não diz nada a quem não decorou a tabela do RTD; o
        nome cabe na coluna e dispensa o tooltip para a leitura do dia a dia.
        """
        return INSTRUMENT_STATUS_LABELS.get(status or "", "Desconhecido")

    @app.template_filter("instrument_status_description")
    def instrument_status_description(status: str | None) -> str:
        """Explicação completa do estado, usada como tooltip da coluna ST."""
        return INSTRUMENT_STATUS_DESCRIPTIONS.get(
            status or "", "Estado de negociação não informado pelo RTD"
        )

    @app.template_filter("movement_label")
    def movement_label(kind: str) -> str:
        """Rótulo de um lançamento do extrato de uma posição
        (``models.PositionMovementKind``)."""
        return POSITION_MOVEMENT_LABELS.get(str(kind), "Movimento")

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

    @app.template_filter("income_kind_label")
    def income_kind_label(value: object) -> str:
        """Rótulo das rendas. "Aluguel" sem qualificação seria ambíguo numa
        tela de investimentos, e "JCP" em maiúsculas é como o mercado
        escreve — nenhum dos dois sai de um ``.title()`` do valor gravado."""
        return INCOME_KIND_LABELS.get(str(getattr(value, "value", value)), str(value))

    @app.template_filter("sign_class")
    def sign_class(value: Decimal | None) -> str:
        if value is None or value == 0:
            return ""
        return "negative" if value < 0 else "positive"
