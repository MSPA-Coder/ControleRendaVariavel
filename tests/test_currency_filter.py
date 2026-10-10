import pytest

from app.core.currency_filter import (
    ALL,
    BRL,
    DEFAULT_CURRENCY_FILTER,
    USD,
    parse_currency_filter,
)


def test_currency_filter_defaults_to_brl():
    assert DEFAULT_CURRENCY_FILTER == BRL
    assert parse_currency_filter({}) == BRL


@pytest.mark.parametrize("value", [BRL, USD, ALL, "usd", " all "])
def test_currency_filter_accepts_supported_values(value):
    assert parse_currency_filter({"currency": value}) in {BRL, USD, ALL}


@pytest.mark.parametrize("value", ["", "EUR", "brl;drop table", "1"])
def test_currency_filter_rejects_untrusted_values(value):
    with pytest.raises(ValueError):
        parse_currency_filter({"currency": value})
