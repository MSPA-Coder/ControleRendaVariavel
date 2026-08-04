from __future__ import annotations

from datetime import date
from decimal import Decimal
from threading import Event, Lock, Thread

import pytest
from flask import Flask
from flask.testing import FlaskClient

import app.position_closure as position_closure
from app import db
from app.models import Broker, Market, Position, PositionKind, Side, Ticker, Transaction

pytestmark = [pytest.mark.critical, pytest.mark.business_rule]


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


def test_transactions_page_defaults_position_filter_to_real(
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
                    average_cost=Decimal("20"),
                    exit_price=Decimal("25"),
                    side=Side.BUY,
                    opened_on=date(2026, 1, 1),
                    closed_on=date(2026, 2, 1),
                    result_mode="L",
                    position_kind=PositionKind.REAL,
                    result=Decimal("499.80000000"),
                ),
                Transaction(
                    broker_id=broker_id,
                    ticker_id=ticker_id,
                    quantity=Decimal("50"),
                    average_cost=Decimal("10"),
                    exit_price=Decimal("15"),
                    side=Side.BUY,
                    opened_on=date(2026, 1, 1),
                    closed_on=date(2026, 2, 1),
                    result_mode="L",
                    position_kind=PositionKind.HYPOTHETICAL,
                    result=Decimal("249.90000000"),
                ),
            ]
        )
        db.session.commit()

    # Sem nenhum parâmetro de query — deve vir pré-selecionado "Real", igual
    # ao padrão de selected_filters() usado em Ações/Carteira, e a tabela deve
    # trazer só a transação real (a hipotética some da listagem).
    response = auth_client.get("/transactions")

    assert response.status_code == 200
    html = response.get_data(as_text=True)

    kind_select_start = html.index('name="position_kind"')
    kind_select_end = html.index("</select>", kind_select_start)
    kind_select = html[kind_select_start:kind_select_end]
    assert 'value="real" selected' in kind_select
    assert 'value="all" selected' not in kind_select

    # Confere que o filtro foi de fato aplicado na consulta (não só no
    # <select> renderizado): só a linha real aparece na tabela.
    tbody = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert tbody.count('<tr class="') == 1
    assert "Hipotética" not in tbody


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
        assert tx.source_position_id == position_id


def test_close_position_is_idempotent_after_first_success(
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

    first = auth_client.post(
        f"/positions/{position_id}/close",
        data={"exit_price": "25", "closed_on": "2026-03-01"},
        follow_redirects=True,
    )
    second = auth_client.post(
        f"/positions/{position_id}/close",
        data={"exit_price": "25", "closed_on": "2026-03-01"},
        follow_redirects=True,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    with app.app_context():
        transactions = db.session.scalars(db.select(Transaction)).all()
        assert len(transactions) == 1


def test_close_position_lock_serializes_competing_writes(
    app: Flask, monkeypatch: pytest.MonkeyPatch
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

    first_calculation_started = Event()
    release_first_write = Event()
    second_attempted = Event()
    second_finished = Event()
    errors: list[BaseException] = []
    outcome: dict[str, Transaction | None] = {}
    calculation_lock = Lock()
    calculations = 0
    original_operation_result = position_closure.operation_result

    def hold_the_first_write(
        side: str,
        quantity: Decimal,
        average_cost: Decimal,
        current_price: Decimal,
        result_mode: str,
    ) -> Decimal:
        nonlocal calculations
        with calculation_lock:
            calculations += 1
            is_first_calculation = calculations == 1
        if is_first_calculation:
            first_calculation_started.set()
            if not release_first_write.wait(timeout=3):
                raise AssertionError("Timed out waiting to release the first writer.")
        return original_operation_result(
            side, quantity, average_cost, current_price, result_mode
        )

    monkeypatch.setattr(position_closure, "operation_result", hold_the_first_write)

    def writer(name: str) -> None:
        try:
            with app.app_context():
                if name == "second":
                    second_attempted.set()
                outcome[name] = position_closure.close_open_position(
                    position_id, Decimal("25"), date(2026, 3, 1)
                )
        except BaseException as exc:  # pragma: no cover - assertion propagation from thread
            errors.append(exc)
        finally:
            if name == "second":
                second_finished.set()

    first_thread = Thread(target=writer, args=("first",))
    second_thread = Thread(target=writer, args=("second",))
    first_thread.start()
    assert first_calculation_started.wait(timeout=3)
    second_thread.start()
    assert second_attempted.wait(timeout=3)
    try:
        assert not second_finished.wait(timeout=0.2)
    finally:
        release_first_write.set()
    first_thread.join(timeout=3)
    second_thread.join(timeout=3)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert not errors
    assert outcome["first"] is not None
    assert outcome["second"] is None
    with app.app_context():
        assert db.session.get(Position, position_id) is None
        transactions = db.session.scalars(
            db.select(Transaction).where(Transaction.source_position_id == position_id)
        ).all()
        assert len(transactions) == 1


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


def test_transactions_page_computes_payoff_ratio_and_avg_days_held(
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
                    exit_price=Decimal("20"),
                    side=Side.BUY,
                    opened_on=date(2026, 1, 1),
                    closed_on=date(2026, 1, 11),  # 10 dias, ganho de 1000
                    result_mode="B",
                    result=Decimal("1000"),
                    position_kind=PositionKind.REAL,
                ),
                Transaction(
                    broker_id=broker_id,
                    ticker_id=ticker_id,
                    quantity=Decimal("100"),
                    average_cost=Decimal("10"),
                    exit_price=Decimal("5"),
                    side=Side.BUY,
                    opened_on=date(2026, 1, 1),
                    closed_on=date(2026, 1, 31),  # 30 dias, perda de 500
                    result_mode="B",
                    result=Decimal("-500"),
                    position_kind=PositionKind.REAL,
                ),
            ]
        )
        db.session.commit()

    response = auth_client.get("/transactions")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # payoff ratio = 1000 / 500 = 2.00
    assert "2,00" in html
    # tempo médio = (10 + 30) / 2 = 20,0 dias
    assert "20,0" in html


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


def test_dividends_page_shows_yield_on_cost_report(app: Flask, auth_client: FlaskClient) -> None:
    with app.app_context():
        broker_id, ticker_id = _seed_broker_ticker()
        db.session.add(
            Position(
                broker_id=broker_id,
                ticker_id=ticker_id,
                quantity=Decimal("100"),
                average_cost=Decimal("10"),  # custo de aquisição = 1000
                side=Side.BUY,
                opened_on=date(2026, 1, 1),
                quote_multiplier=Decimal("1"),
                target_multiplier=Decimal("1.5"),
                result_mode="L",
                position_kind=PositionKind.REAL,
            )
        )
        db.session.commit()

    auth_client.post(
        "/dividends",
        data={
            "broker_id": str(broker_id),
            "ticker_id": str(ticker_id),
            "amount": "80",
            "payment_date": "2026-02-15",
        },
    )

    response = auth_client.get("/dividends")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "PETR4" in html
    # yield on cost = 80 / 1000 = 8,00%
    assert "8,00%" in html
    assert "02/2026" in html


@pytest.mark.security
def test_transactions_and_dividends_require_authentication(client: FlaskClient) -> None:
    assert client.get("/transactions").status_code == 302
    assert client.get("/dividends").status_code == 302
