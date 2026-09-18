"""Contrato do filtro de moeda usado pela apresentação global."""

from __future__ import annotations

from collections.abc import Mapping

BRL = "BRL"
USD = "USD"
ALL = "ALL"
DEFAULT_CURRENCY_FILTER = BRL
CURRENCY_FILTER_VALUES = frozenset({BRL, USD, ALL})


def parse_currency_filter(params: Mapping[str, str]) -> str:
    """Valida ``currency=...`` da URL, rejeitando valores arbitrários.

    O filtro é deliberadamente request-scoped: não é salvo em sessão nem em
    banco. Assim duas abas podem escolher moedas diferentes sem corrida ou
    uma resposta antiga sobrescrever a preferência da outra.
    """
    value = params.get("currency", DEFAULT_CURRENCY_FILTER).strip().upper()
    if value not in CURRENCY_FILTER_VALUES:
        raise ValueError("Selecione uma moeda válida.")
    return value


def currency_matches(selected: str, currency: str) -> bool:
    """Diz se um registro deve aparecer no filtro selecionado."""
    return selected in (ALL, currency)
