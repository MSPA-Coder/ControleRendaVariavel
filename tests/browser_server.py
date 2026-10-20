"""Servidor exclusivo do piloto de navegador; nunca usa dados operacionais."""

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from flask_migrate import upgrade

from app import create_app, db
from app.models import (
    Broker,
    Dividend,
    Execution,
    ExecutionImport,
    IncomeKind,
    Market,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionQuote,
    OptionType,
    Portfolio,
    Position,
    Quote,
    QuoteHistory,
    Side,
    Ticker,
    Transaction,
    User,
    UserPreference,
    UserTickerEntitlement,
)
from app.options.closure import create_or_merge_position as create_or_merge_option
from app.positions.closure import create_or_merge_position


def seed_interface_demo():
    """Cenário fictício para validar a interface no banco descartável."""
    today = date.today()
    user = User(username="browser-demo", role="operador", must_change_password=False)
    user.set_password("Synthetic-browser-only-2026!")
    brokers = [Broker(name=name, acronym=code) for name, code in (
        ("Genial · demonstração", "DEMO-G"), ("XP · demonstração", "DEMO-X"),
    )]
    db.session.add_all([user, *brokers])
    db.session.flush()
    real = Portfolio(owner_id=user.id, name="Principal · dados fictícios", currency="BRL")
    db.session.add_all([real, UserPreference(user_id=user.id, regional_format="br", theme="institutional")])
    db.session.flush()
    rows = [
        ("PETR4", "Petrobras", "1000", "32.10", "36.42", 0),
        ("VALE3", "Vale", "300", "66.40", "61.80", 1),
        ("ITUB4", "Itaú Unibanco", "800", "30.80", "34.75", 0),
        ("WEGE3", "WEG", "200", "42.60", "48.20", 1),
        ("BBAS3", "Banco do Brasil", "500", "22.40", "24.16", 0),
        ("BOVA11", "iShares Ibovespa", "100", "125.80", "130.40", 1),
        ("SEM3", "Ativo fictício sem cotação", "50", "10", None, 0),
    ]
    tickers = {}
    for symbol, name, qty, cost, price, broker_idx in rows:
        ticker = Ticker(symbol=symbol, trading_name=name, market=Market.B3, currency="BRL", rtd_market_code="B")
        db.session.add(ticker)
        db.session.flush()
        tickers[symbol] = ticker
        opened = today - timedelta(days=160)
        create_or_merge_position(Position(
            owner_id=user.id, broker_id=brokers[broker_idx].id, ticker_id=ticker.id,
            portfolio_id=real.id, quantity=Decimal(qty), average_cost=Decimal(cost),
            side=Side.BUY, opened_on=opened, target_multiplier=Decimal("1.2"),
        ))
        db.session.add(UserTickerEntitlement(user_id=user.id, ticker_id=ticker.id, first_held_on=opened))
        if price is not None:
            current = Decimal(price)
            db.session.add(Quote(ticker_id=ticker.id, last_price=current,
                                 previous_close=current * Decimal("0.99"), observed_at=datetime.now(UTC)))
            for day in range(0, 161, 4):
                db.session.add(QuoteHistory(ticker_id=ticker.id, recorded_date=opened + timedelta(days=day),
                                           price=Decimal(cost) + (current - Decimal(cost)) * Decimal(day) / Decimal(160),
                                           recorded_at=datetime.now(UTC)))
    # Segundo aporte: exercita a abertura e o aumento no extrato da posição.
    create_or_merge_position(Position(
        owner_id=user.id, broker_id=brokers[0].id, ticker_id=tickers["PETR4"].id,
        portfolio_id=real.id, quantity=Decimal("100"), average_cost=Decimal("34"),
        side=Side.BUY, opened_on=today - timedelta(days=60), target_multiplier=Decimal("1.2"),
    ))
    expirations = []
    for index, remaining in enumerate((4, 32, 60)):
        expiration = OptionExpiration(call_code=f"DM{index}C", put_code=f"DM{index}P",
                                      exercise_date=today + timedelta(days=remaining))
        db.session.add(expiration)
        expirations.append(expiration)
    db.session.flush()
    for symbol, underlying, kind, side, strike, qty, cost, price, expiry_idx, broker_idx in (
        ("PETR-DM-C", "PETR4", OptionType.CALL, Side.SELL, "38", "1000", "1.20", ".48", 0, 0),
        ("VALE-DM-P", "VALE3", OptionType.PUT, Side.SELL, "60", "500", "1.65", "2.10", 1, 1),
        ("ITUB-DM-C", "ITUB4", OptionType.CALL, Side.BUY, "35", "800", ".80", ".96", 2, 0),
        ("BBAS-DM-P", "BBAS3", OptionType.PUT, Side.SELL, "24", "1000", ".62", ".28", 0, 1),
    ):
        ticker = Ticker(symbol=symbol, trading_name="Contrato fictício", market=Market.B3,
                        currency="BRL", rtd_market_code="B")
        db.session.add(ticker)
        db.session.flush()
        contract = OptionContract(ticker_id=ticker.id, underlying_ticker_id=tickers[underlying].id,
                                  expiration_id=expirations[expiry_idx].id, option_type=kind, strike=Decimal(strike))
        db.session.add(contract)
        db.session.flush()
        create_or_merge_option(OptionPosition(
            owner_id=user.id, broker_id=brokers[broker_idx].id, contract_id=contract.id,
            portfolio_id=real.id, quantity=Decimal(qty), average_cost=Decimal(cost), side=side,
            target_price=Decimal("0.2"), opened_on=today - timedelta(days=21),
        ))
        under_price = next(row[4] for row in rows if row[0] == underlying)
        db.session.add(OptionQuote(contract_id=contract.id, last_price=Decimal(price),
                                   previous_close=Decimal(price) * Decimal("1.02"),
                                   underlying_price=Decimal(under_price), observed_at=datetime.now(UTC)))
        db.session.add(UserTickerEntitlement(user_id=user.id, ticker_id=ticker.id,
                                             first_held_on=today - timedelta(days=21)))

    # Recebimentos sintéticos: origem CB, planilha, bruto e retenções.
    for index, (symbol, kind, amount, origin) in enumerate((
        ("PETR4", IncomeKind.DIVIDENDO, "1240.50", "cb"),
        ("PETR4", IncomeKind.JCP, "382.50", "cb"),
        ("ITUB4", IncomeKind.JCP, "168", "cb"),
        ("BBAS3", IncomeKind.DIVIDENDO, "245", "planilha"),
    )):
        db.session.add(Dividend(
            owner_id=user.id, broker_id=brokers[0].id, ticker_id=tickers[symbol].id,
            amount=Decimal(amount), kind=kind, origin=origin,
            origin_id=f"demo-income-{index}" if origin == "cb" else None,
            gross_amount=Decimal("450") if index == 1 else Decimal(amount),
            withheld_amount=Decimal("67.50") if index == 1 else Decimal("0"),
            payment_date=today - timedelta(days=30 + index * 15),
            com_date=today - timedelta(days=35 + index * 15),
            notes="Recebimento fictício para validar a navegação; não veio de uma integração real.",
        ))
    for symbol, quantity, cost, exit_price in (
        ("PETR4", "100", "31", "36"), ("VALE3", "50", "67", "62"),
    ):
        db.session.add(Transaction(
            owner_id=user.id, broker_id=brokers[0].id, ticker_id=tickers[symbol].id,
            portfolio_id=real.id, side=Side.BUY, quantity=Decimal(quantity),
            average_cost=Decimal(cost), exit_price=Decimal(exit_price),
            opened_on=today - timedelta(days=120), closed_on=today - timedelta(days=12),
            result=(Decimal(exit_price) - Decimal(cost)) * Decimal(quantity),
            notes="Operação histórica fictícia, independente das posições abertas da demonstração.",
        ))
    batch = ExecutionImport(
        owner_id=user.id, file_name="demonstracao-ficticia.xlsx", period_start=today - timedelta(days=10),
        period_end=today, sha256="d" * 64, row_count=2, rows_created=2, rows_updated=0,
    )
    db.session.add(batch)
    db.session.flush()
    for index in range(2):
        db.session.add(Execution(
            owner_id=user.id, import_id=batch.id, broker_id=brokers[index].id,
            institution="demonstracao", trade_date=today - timedelta(days=10 - index),
            document=f"NOTA-DEMO-{index + 1}", line_order=1, content_hash=str(index) * 64,
            asset="PETR4", asset_group="acao", side="C", cash_operation="D",
            quantity=Decimal("100"), price_without_fees=Decimal("35"), price_with_fees=Decimal("35.01"),
            fees_total=Decimal("1"), total_without_fees=Decimal("-3500"), total_with_fees=Decimal("-3501"),
            day_trade=False, currency="BRL", in_scope=True,
        ))


def seed_browser_scenario():
    """Cria o cenário somente quando o banco exclusivo do navegador está vazio."""
    admin = User(username="browser-admin", role="admin", must_change_password=False)
    admin.set_password("Synthetic-browser-only-2026!")
    db.session.add(admin)
    broker = Broker(name="Corretora browser", acronym="BROWSER")
    tickers = [Ticker(symbol=symbol, trading_name=symbol, market=Market.B3, currency="BRL", rtd_market_code="B")
               for symbol in ("BROWSE3", "RETRY3", "FLOW3")]
    db.session.add_all([broker, *tickers])
    db.session.flush()
    for region in ("br", "us", "flow"):
        user = User(username=f"browser-{region}", role="operador", must_change_password=False)
        user.set_password("Synthetic-browser-only-2026!")
        db.session.add(user)
        db.session.flush()
        portfolio = Portfolio(owner_id=user.id, name="Carteira browser", currency="BRL")
        db.session.add_all([portfolio, UserPreference(user_id=user.id, regional_format="us" if region == "us" else "br")])
        db.session.flush()
        if region == "flow":
            create_or_merge_position(Position(
                owner_id=user.id, broker_id=broker.id, ticker_id=tickers[2].id,
                portfolio_id=portfolio.id, quantity=Decimal("20"), average_cost=Decimal("10"),
                side=Side.BUY, opened_on=date(2026, 6, 10), target_multiplier=Decimal("1.5"),
            ))
            db.session.add(UserTickerEntitlement(user_id=user.id, ticker_id=tickers[2].id, first_held_on=date(2026, 6, 10)))
    for ticker in tickers:
        db.session.add(Quote(ticker_id=ticker.id, last_price=Decimal("22.50"), previous_close=Decimal("22"), observed_at=datetime.now(UTC)))
    seed_interface_demo()
    db.session.commit()


def main():
    if os.environ.get("BROWSER_TEST_SERVER") != "1":
        raise RuntimeError("Servidor restrito ao Compose de testes de navegador.")
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "browser-tests-only-not-an-operational-secret",
        "SESSION_COOKIE_NAME": os.environ.get("BROWSER_SESSION_COOKIE_NAME", "crv_browser_test_session"),
        "REMEMBER_COOKIE_NAME": os.environ.get("BROWSER_REMEMBER_COOKIE_NAME", "crv_browser_test_remember"),
        "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://browser:browser@browser-db:5432/browser_tests",
    })

    @app.after_request
    def identify_disposable_preview(response):
        if response.status_code == 200 and response.mimetype == "text/html":
            response.set_data(response.get_data(as_text=True).replace(
                "<main>",
                '<main><p class="trade-context-note" role="note">Prévia local · dados fictícios. '
                'Cotações estáticas; alterações afetam somente o cenário descartável.</p>',
                1,
            ))
        return response

    with app.app_context():
        upgrade()
        populated = db.session.query(User).count() > 0
        if populated and os.environ.get("BROWSER_REUSE_SCENARIO") != "1":
            raise RuntimeError("Banco do navegador deve nascer vazio; recrie a pilha de teste.")
        if not populated:
            seed_browser_scenario()
    app.run(host="0.0.0.0", port=5003, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
