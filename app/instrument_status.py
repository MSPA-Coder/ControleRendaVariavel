"""Estado de negociação do papel, como o RTD o informa.

O servidor RTD devolve o estado no campo ``EST``; só a primeira letra
importa. Cada estado muda o que se pode fazer com a posição, e por isso cada
um tem rótulo e cor próprios nas telas de Ações e Opções — ver
``docs/planilha-acoes.md``.

Vive em módulo próprio porque as duas telas montam suas views em lugares
diferentes (``app.portfolio`` e ``app.option_portfolio``) e precisam do mesmo
mapeamento; duplicá-lo deixaria as duas grades divergirem em silêncio.
"""

from __future__ import annotations

from typing import Protocol


class _HasInstrumentStatus(Protocol):
    instrument_status: str


INSTRUMENT_STATUS_CLASSES = {
    "P": "pre-open",
    "A": "open",
    "L": "auction",
    "F": "closed",
    "S": "suspended",
}

INSTRUMENT_STATUS_LABELS = {
    "P": "Pré-abertura",
    "A": "Aberto",
    "L": "Leilão",
    "F": "Fechado",
    "S": "Suspenso",
}

INSTRUMENT_STATUS_DESCRIPTIONS = {
    "P": "Pré-abertura: o mercado calcula o preço de equilíbrio e aceita ordens, mas não executa",
    "A": "Aberto: negociação contínua, a ordem executa assim que houver contraparte",
    "L": "Leilão: o ativo saiu do túnel de preço ou está no leilão de fechamento",
    "F": "Fechado: o pregão e os leilões deste ativo não estão em curso",
    "S": "Suspenso: negociação travada pela B3/CVM (fato relevante ou irregularidade)",
}

UNKNOWN_CLASS = "unknown"


def instrument_status_letter(quote: _HasInstrumentStatus | None) -> str:
    """Primeira letra do estado informado pelo RTD, ou vazio sem cotação."""
    if quote is None:
        return ""
    return str(getattr(quote, "instrument_status", "")).strip().upper()[:1]


def instrument_status_class(letter: str) -> str:
    return INSTRUMENT_STATUS_CLASSES.get(letter, UNKNOWN_CLASS)
