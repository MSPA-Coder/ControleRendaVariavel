import pytest

from app.core.currency_filter import (
    ALL,
    BRL,
    DEFAULT_CURRENCY_FILTER,
    USD,
    currency_matches,
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


def test_all_matches_each_currency_without_merging_totals():
    assert currency_matches(ALL, BRL)
    assert currency_matches(ALL, USD)
    assert not currency_matches(BRL, USD)
