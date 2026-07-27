import pytest

from app.models import Market
from app.reference_data import parse_broker_name, parse_ticker


def test_broker_name_is_normalized() -> None:
    assert parse_broker_name({"name": "  Banco   Teste  "}) == "Banco Teste"


def test_ticker_fields_are_normalized() -> None:
    ticker = parse_ticker(
        {
            "symbol": " bbas3 ",
            "market": "B3",
            "rtd_market_code": "b",
            "currency": "brl",
        }
    )

    assert ticker.symbol == "BBAS3"
    assert ticker.market == Market.B3
    assert ticker.rtd_market_code == "B"
    assert ticker.currency == "BRL"


@pytest.mark.parametrize(
    "form",
    [
        {"symbol": "", "market": "B3", "rtd_market_code": "B", "currency": "BRL"},
        {"symbol": "TEST3", "market": "OUTRO", "rtd_market_code": "B", "currency": "BRL"},
        {"symbol": "TEST3", "market": "B3", "rtd_market_code": "X", "currency": "BRL"},
        {"symbol": "TEST3", "market": "B3", "rtd_market_code": "B", "currency": "EUR"},
    ],
)
def test_ticker_rejects_invalid_catalog_values(form: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        parse_ticker(form)
