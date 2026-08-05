from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from flask import Flask

from app import db
from app.models import (
    Broker,
    Market,
    OptionContract,
    OptionExpiration,
    OptionType,
    Position,
    PositionKind,
    Side,
    Ticker,
)
from app.routes.helpers import (
    DEFAULT_BENCHMARK_IMPORT_LOOKBACK_DAYS,
    benchmark_candidates,
    investable_ticker_records,
    open_real_quantities_by_ticker,
    quote_update_targets,
    ticker_has_holdings,
)

pytestmark = [pytest.mark.critical, pytest.mark.business_rule]


def test_open_real_quantities_by_ticker_applies_side_signal(app: Flask) -> None:
    with app.app_context():
        broker = Broker(name="XP", acronym="XP")
        ticker = Ticker(
            symbol="PETR4",
            trading_name="Petrobras",
            market=Market.B3,
            rtd_market_code="B",
            currency="BRL",
        )
        db.session.add_all([broker, ticker])
        db.session.commit()
        db.session.add_all(
            [
                Position(
                    broker_id=broker.id,
                    ticker_id=ticker.id,
                    quantity=Decimal("100"),
                    average_cost=Decimal("20"),
                    side=Side.BUY,
                    opened_on=date(2026, 1, 1),
                    quote_multiplier=Decimal("1"),
                    target_multiplier=Decimal("1.5"),
                    result_mode="L",
                    position_kind=PositionKind.REAL,
                ),
                Position(
                    broker_id=broker.id,
                    ticker_id=ticker.id,
                    quantity=Decimal("40"),
                    average_cost=Decimal("25"),
                    side=Side.SELL,
                    opened_on=date(2026, 1, 2),
                    quote_multiplier=Decimal("1"),
                    target_multiplier=Decimal("1.5"),
                    result_mode="L",
                    position_kind=PositionKind.REAL,
                ),
            ]
        )
        db.session.commit()

        assert open_real_quantities_by_ticker() == {ticker.id: Decimal("60")}


def _seed_ticker(symbol: str, *, is_benchmark: bool = False, currency: str = "BRL") -> Ticker:
    ticker = Ticker(
        symbol=symbol,
        trading_name=symbol,
        market=Market.B3,
        rtd_market_code="B",
        currency=currency,
        is_benchmark=is_benchmark,
    )
    db.session.add(ticker)
    db.session.commit()
    return ticker


def test_investable_ticker_records_excludes_benchmarks(app: Flask) -> None:
    with app.app_context():
        stock = _seed_ticker("PETR4")
        _seed_ticker("BOVA11", is_benchmark=True)

        symbols = [ticker.symbol for ticker in investable_ticker_records()]

        assert symbols == [stock.symbol]


def test_benchmark_candidates_only_returns_benchmarks_in_alphabetical_order(app: Flask) -> None:
    with app.app_context():
        _seed_ticker("PETR4")
        _seed_ticker("USDBRL=X", is_benchmark=True)
        bova = _seed_ticker("BOVA11", is_benchmark=True)

        symbols = [ticker.symbol for ticker in benchmark_candidates()]

        assert symbols == ["BOVA11", "USDBRL=X"]
        assert [ticker.symbol for ticker in benchmark_candidates(exclude_ticker_id=bova.id)] == [
            "USDBRL=X"
        ]


def test_ticker_has_holdings_true_for_a_ticker_with_a_position(app: Flask) -> None:
    with app.app_context():
        broker = Broker(name="XP", acronym="XP")
        ticker = _seed_ticker("PETR4")
        db.session.add(broker)
        db.session.commit()
        db.session.add(
            Position(
                broker_id=broker.id,
                ticker_id=ticker.id,
                quantity=Decimal("10"),
                average_cost=Decimal("20"),
                side=Side.BUY,
                opened_on=date(2026, 1, 1),
                quote_multiplier=Decimal("1"),
                target_multiplier=Decimal("1.5"),
                result_mode="L",
                position_kind=PositionKind.REAL,
            )
        )
        db.session.commit()

        assert ticker_has_holdings(ticker.id) is True


def test_ticker_has_holdings_true_for_an_option_underlying(app: Flask) -> None:
    with app.app_context():
        underlying = _seed_ticker("PETR4")
        option_ticker = _seed_ticker("PETRA123")
        expiration = OptionExpiration(
            call_code="27A01", put_code="27M01", exercise_date=date(2027, 1, 18)
        )
        db.session.add(expiration)
        db.session.commit()
        db.session.add(
            OptionContract(
                ticker_id=option_ticker.id,
                underlying_ticker_id=underlying.id,
                expiration_id=expiration.id,
                option_type=OptionType.CALL,
                strike=Decimal("30"),
            )
        )
        db.session.commit()

        assert ticker_has_holdings(underlying.id) is True
        assert ticker_has_holdings(option_ticker.id) is True


def test_ticker_has_holdings_false_for_an_unused_ticker(app: Flask) -> None:
    with app.app_context():
        ticker = _seed_ticker("BOVA11")

        assert ticker_has_holdings(ticker.id) is False


def test_quote_update_targets_includes_benchmarks_without_a_position(app: Flask) -> None:
    with app.app_context():
        broker = Broker(name="XP", acronym="XP")
        stock = _seed_ticker("PETR4")
        benchmark = _seed_ticker("BOVA11", is_benchmark=True)
        db.session.add(broker)
        db.session.commit()
        opened_on = date(2026, 3, 10)
        db.session.add(
            Position(
                broker_id=broker.id,
                ticker_id=stock.id,
                quantity=Decimal("10"),
                average_cost=Decimal("20"),
                side=Side.BUY,
                opened_on=opened_on,
                quote_multiplier=Decimal("1"),
                target_multiplier=Decimal("1.5"),
                result_mode="L",
                position_kind=PositionKind.REAL,
            )
        )
        db.session.commit()

        targets = dict(
            (target.id, start) for target, start in quote_update_targets()
        )

        # A ação usa a própria data de abertura; o benchmark, sem posição
        # própria, usa a data de abertura mais antiga de TODA a carteira —
        # é isso que evita precisar de uma posição fantasma para ele.
        assert targets[stock.id] == opened_on
        assert targets[benchmark.id] == opened_on


def test_quote_update_targets_uses_lookback_default_without_any_position(app: Flask) -> None:
    with app.app_context():
        benchmark = _seed_ticker("BOVA11", is_benchmark=True)

        targets = dict((target.id, start) for target, start in quote_update_targets())

        expected_start = date.today() - timedelta(days=DEFAULT_BENCHMARK_IMPORT_LOOKBACK_DAYS)
        assert targets[benchmark.id] == expected_start


def test_quote_update_targets_prefers_the_tickers_own_position_start(app: Flask) -> None:
    # Caso de borda: um ticker marcado como benchmark que ainda tem uma
    # posição própria (dado legado) usa a data mais específica dessa
    # posição, não a data global — o cadastro (Tabelas > Tickers) já
    # bloqueia criar essa combinação a partir de agora, mas a consulta
    # continua correta se ela existir de qualquer forma.
    with app.app_context():
        broker = Broker(name="XP", acronym="XP")
        benchmark = _seed_ticker("BOVA11", is_benchmark=True)
        other_stock = _seed_ticker("PETR4")
        db.session.add(broker)
        db.session.commit()
        db.session.add_all(
            [
                Position(
                    broker_id=broker.id,
                    ticker_id=other_stock.id,
                    quantity=Decimal("10"),
                    average_cost=Decimal("20"),
                    side=Side.BUY,
                    opened_on=date(2026, 1, 1),
                    quote_multiplier=Decimal("1"),
                    target_multiplier=Decimal("1.5"),
                    result_mode="L",
                    position_kind=PositionKind.REAL,
                ),
                Position(
                    broker_id=broker.id,
                    ticker_id=benchmark.id,
                    quantity=Decimal("5"),
                    average_cost=Decimal("10"),
                    side=Side.BUY,
                    opened_on=date(2026, 6, 1),
                    quote_multiplier=Decimal("1"),
                    target_multiplier=Decimal("1.5"),
                    result_mode="L",
                    position_kind=PositionKind.REAL,
                ),
            ]
        )
        db.session.commit()

        targets = dict((target.id, start) for target, start in quote_update_targets())

        assert targets[benchmark.id] == date(2026, 6, 1)
