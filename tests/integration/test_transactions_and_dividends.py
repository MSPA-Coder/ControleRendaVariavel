from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Broker, Market, Position, PositionKind, Side, Ticker, Transaction


def _seed_broker_ticker() -> tuple[int, int]:
    broker = Broker(name="XP Investimentos", acronym="XP")
    ticker = Ticker(
        symbol="PETR4",
        trading_name="Petrobras",
        market=Market.B3,
        rtd_market_code="B",
        currency="BRL",
    )
    db.session.add_all([broker, ticker])
    db.session.commit()
    return broker.id, ticker.id


def test_transaction_full_crud_roundtrip(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_broker_ticker()

    create = auth_client.post(
        "/transactions",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(ticker_id),
            "quantity": "100",
            "average_cost": "20",
            "exit_price": "25",
            "side": "C",
            "opened_on": "2026-01-01",
            "closed_on": "2026-03-01",
            "result_mode": "L",
            "position_kind": "real",
        },
        follow_redirects=True,
    )
    assert create.status_code == 200

    with app.app_context():
        tx = db.session.scalar(db.select(Transaction))
        assert tx is not None
        # (25-20)*100*0.9996 = 499.8
        assert tx.result == Decimal("499.80000000")
        tx_id = tx.id

    update = auth_client.post(
        f"/transactions/{tx_id}",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(ticker_id),
            "quantity": "100",
            "average_cost": "20",
            "exit_price": "30",
            "side": "C",
            "opened_on": "2026-01-01",
            "closed_on": "2026-03-01",
            "result_mode": "L",
            "position_kind": "real",
        },
        follow_redirects=True,
    )
    assert update.status_code == 200
    with app.app_context():
        tx = db.session.get(Transaction, tx_id)
        assert tx is not None
        assert tx.result == Decimal("999.60000000")  # (30-20)*100*0.9996

    delete = auth_client.post(f"/transactions/{tx_id}/delete", follow_redirects=True)
    assert delete.status_code == 200
    with app.app_context():
        assert db.session.get(Transaction, tx_id) is None


def test_transaction_rejects_closed_before_opened(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_broker_ticker()

    response = auth_client.post(
        "/transactions",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(ticker_id),
            "quantity": "100",
            "average_cost": "20",
            "exit_price": "25",
            "side": "C",
            "opened_on": "2026-03-01",
            "closed_on": "2026-01-01",
            "result_mode": "L",
            "position_kind": "real",
        },
    )
    assert response.status_code == 422
    with app.app_context():
        assert db.session.scalar(db.select(Transaction)) is None


def test_dividend_full_crud_roundtrip(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_broker_ticker()

    create = auth_client.post(
        "/dividends",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(ticker_id),
            "amount": "12.50",
            "payment_date": "2026-02-15",
        },
        follow_redirects=True,
    )
    assert create.status_code == 200

    from app.models import Dividend

    with app.app_context():
        dividend = db.session.scalar(db.select(Dividend))
        assert dividend is not None
        assert dividend.amount == Decimal("12.50")
        dividend_id = dividend.id

    delete = auth_client.post(f"/dividends/{dividend_id}/delete", follow_redirects=True)
    assert delete.status_code == 200
    with app.app_context():
        assert db.session.get(Dividend, dividend_id) is None


def test_dividend_rejects_non_positive_amount(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_broker_ticker()

    response = auth_client.post(
        "/dividends",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(ticker_id),
            "amount": "0",
            "payment_date": "2026-02-15",
        },
    )
    assert response.status_code == 422


def test_close_position_creates_transaction_and_removes_position(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_broker_ticker()
        position = Position(
            broker_id=broker_id,
            ticker_id=ticker_id,
            quantity=Decimal("100"),
            average_cost=Decimal("20"),
            side=Side.BUY,
            opened_on=date(2026, 1, 1),
            quote_multiplier=Decimal("1"),
            target_multiplier=Decimal("1.5"),
            result_mode="L",
            position_kind=PositionKind.REAL,
        )
        db.session.add(position)
        db.session.commit()
        position_id = position.id

    response = auth_client.post(
        f"/positions/{position_id}/close",
        data={"exit_price": "25", "closed_on": "2026-03-01"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        assert db.session.get(Position, position_id) is None
        tx = db.session.scalar(db.select(Transaction))
        assert tx is not None
        assert tx.result == Decimal("499.80000000")
        assert tx.broker_id == broker_id
        assert tx.ticker_id == ticker_id


def test_close_position_rejects_closed_before_opened(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_broker_ticker()
        position = Position(
            broker_id=broker_id,
            ticker_id=ticker_id,
            quantity=Decimal("100"),
            average_cost=Decimal("20"),
            side=Side.BUY,
            opened_on=date(2026, 3, 1),
            quote_multiplier=Decimal("1"),
            target_multiplier=Decimal("1.5"),
            result_mode="L",
            position_kind=PositionKind.REAL,
        )
        db.session.add(position)
        db.session.commit()
        position_id = position.id

    auth_client.post(
        f"/positions/{position_id}/close",
        data={"exit_price": "25", "closed_on": "2026-01-01"},
    )

    with app.app_context():
        # posição preservada, nenhuma transação criada
        assert db.session.get(Position, position_id) is not None
        assert db.session.scalar(db.select(Transaction)) is None


def test_transactions_page_computes_win_rate_and_profit_factor(
    app: Flask, auth_client: FlaskClient
) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_broker_ticker()
        db.session.add_all(
            [
                Transaction(
                    broker_id=broker_id,
                    ticker_id=ticker_id,
                    quantity=Decimal("100"),
                    average_cost=Decimal("10"),
                    exit_price=Decimal("20"),  # ganho de ~1000
                    side=Side.BUY,
                    opened_on=date(2026, 1, 1),
                    closed_on=date(2026, 2, 1),
                    result_mode="L",
                    result=Decimal("999.6"),
                    position_kind=PositionKind.REAL,
                ),
                Transaction(
                    broker_id=broker_id,
                    ticker_id=ticker_id,
                    quantity=Decimal("100"),
                    average_cost=Decimal("10"),
                    exit_price=Decimal("5"),  # perda de ~500
                    side=Side.BUY,
                    opened_on=date(2026, 1, 1),
                    closed_on=date(2026, 2, 1),
                    result_mode="L",
                    result=Decimal("-499.8"),
                    position_kind=PositionKind.REAL,
                ),
                Transaction(
                    broker_id=broker_id,
                    ticker_id=ticker_id,
                    quantity=Decimal("100"),
                    average_cost=Decimal("10"),
                    exit_price=Decimal("15"),  # ganho de ~500
                    side=Side.BUY,
                    opened_on=date(2026, 1, 1),
                    closed_on=date(2026, 2, 1),
                    result_mode="L",
                    result=Decimal("499.8"),
                    position_kind=PositionKind.REAL,
                ),
            ]
        )
        db.session.commit()

    response = auth_client.get("/transactions")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # 2 de 3 transações positivas -> win rate 66,7%
    assert "66,7%" in html
    # profit factor = (999.6+499.8) / 499.8 = 3.0
    assert "3,00" in html


def test_broker_with_transaction_cannot_be_deleted(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_broker_ticker()
        db.session.add(
            Transaction(
                broker_id=broker_id,
                ticker_id=ticker_id,
                quantity=Decimal("100"),
                average_cost=Decimal("10"),
                exit_price=Decimal("20"),
                side=Side.BUY,
                opened_on=date(2026, 1, 1),
                closed_on=date(2026, 2, 1),
                result_mode="L",
                result=Decimal("999.6"),
                position_kind=PositionKind.REAL,
            )
        )
        db.session.commit()

    response = auth_client.post(f"/tables/brokers/{broker_id}/delete", follow_redirects=True)

    assert response.status_code == 200
    assert "não pode ser excluída" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Broker, broker_id) is not None


def test_transactions_and_dividends_require_authentication(client: FlaskClient) -> None:
    assert client.get("/transactions").status_code == 302
    assert client.get("/dividends").status_code == 302
