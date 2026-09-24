"""Isolamento financeiro exercitado por HTTP com sessão e CSRF reais."""
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app import db
from app.models import (
    Broker,
    Dividend,
    IncomeKind,
    Market,
    OptionContract,
    OptionExpiration,
    OptionPosition,
    OptionPositionMovement,
    OptionType,
    Portfolio,
    Position,
    PositionLedgerArchive,
    PositionMovement,
    Side,
    Ticker,
    Transaction,
    User,
    UserPreference,
    UserTickerEntitlement,
)

pytestmark = pytest.mark.banco


class TokenParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == 'input' and fields.get('name') == 'csrf_token':
            self.token = fields.get('value')


@pytest.fixture
def review_case(app_com_banco):
    app = app_com_banco
    suffix = uuid4().hex[:8]
    with app.app_context():
        users = [User(username=f'review-{suffix}-{i}', role=role, is_active_user=True,
                      must_change_password=False) for i, role in enumerate(['operador', 'admin', 'operador'])]
        for user in users:
            user.set_password('Synthetic-review-only-2026!')
        broker = Broker(name=f'Review {suffix}', acronym=suffix[:6])
        tickers = [Ticker(symbol=f'R{suffix}{i}', trading_name=f'Review asset {i}',
                          market=Market.B3, currency='BRL', rtd_market_code='B') for i in range(3)]
        expiry = OptionExpiration(call_code=suffix[:5], put_code=suffix[3:8],
                                  exercise_date=date.today() + timedelta(days=2000 + int(suffix[:3], 16)))
        db.session.add_all([*users, broker, *tickers, expiry])
        db.session.flush()
        portfolios = [Portfolio(owner_id=u.id, name=f'PRIVATE-{suffix}-{i}', currency='BRL', simulated=False)
                      for i, u in enumerate(users)]
        contract = OptionContract(ticker_id=tickers[1].id, underlying_ticker_id=tickers[0].id,
                                  expiration_id=expiry.id, option_type=OptionType.CALL, strike=Decimal('10'))
        db.session.add_all([*portfolios, contract])
        db.session.flush()
        owner = users[2]
        position = Position(owner_id=owner.id, broker_id=broker.id, ticker_id=tickers[0].id,
                            portfolio_id=portfolios[2].id, quantity=Decimal('17'), average_cost=Decimal('23'),
                            target_multiplier=1, side=Side.BUY, opened_on=date.today(), result_mode='L')
        option = OptionPosition(owner_id=owner.id, broker_id=broker.id, contract_id=contract.id,
                                portfolio_id=portfolios[2].id, quantity=Decimal('19'), average_cost=Decimal('2'),
                                side=Side.BUY, opened_on=date.today(), result_mode='L')
        dividend = Dividend(owner_id=owner.id, broker_id=broker.id, ticker_id=tickers[0].id,
                            amount=Decimal('123.45'), payment_date=date.today(), kind=IncomeKind.DIVIDENDO,
                            notes=f'PRIVATE-DIVIDEND-{suffix}')
        transaction = Transaction(owner_id=owner.id, broker_id=broker.id, ticker_id=tickers[0].id,
                                  portfolio_id=portfolios[2].id, quantity=3, average_cost=10,
                                  exit_price=11, opened_on=date.today(), closed_on=date.today(),
                                  result=3, notes=f'PRIVATE-TRANSACTION-{suffix}')
        db.session.add_all([position, option, dividend, transaction])
        db.session.commit()
        result = {'ids': [u.get_id() for u in users], 'portfolio': portfolios[2].id,
                      'position': position.id, 'option': option.id, 'dividend': dividend.id,
                      'ticker': tickers[0].id, 'broker': broker.id, 'names': [p.name for p in portfolios],
                      'dividend_note': dividend.notes, 'portfolio_ids': [p.id for p in portfolios],
                      'user_ids': [u.id for u in users], 'ticker_ids': [t.id for t in tickers],
                      'contract': contract.id, 'expiration': expiry.id, 'transaction': transaction.id,
                      'transaction_note': transaction.notes}
    try:
        yield app, result
    finally:
        with app.app_context():
            db.session.rollback()
            for model in (PositionMovement, OptionPositionMovement, PositionLedgerArchive,
                          Transaction, Position, OptionPosition, Dividend, Portfolio):
                db.session.execute(delete(model).where(model.owner_id.in_(result['user_ids'])))
            for model in (UserPreference, UserTickerEntitlement):
                db.session.execute(delete(model).where(model.user_id.in_(result['user_ids'])))
            db.session.execute(delete(OptionContract).where(OptionContract.id == result['contract']))
            db.session.execute(delete(OptionExpiration).where(OptionExpiration.id == result['expiration']))
            db.session.execute(delete(Ticker).where(Ticker.id.in_(result['ticker_ids'])))
            db.session.execute(delete(Broker).where(Broker.id == result['broker']))
            db.session.execute(delete(User).where(User.id.in_(result['user_ids'])))
            db.session.commit()


def client_for(app, session_id):
    client = app.test_client()
    suffix = uuid4().hex
    client.environ_base['REMOTE_ADDR'] = '2001:db8::' + ':'.join(suffix[i:i + 4] for i in range(0, 16, 4))
    response = client.get('/login')
    parser = TokenParser()
    parser.feed(response.get_data(as_text=True))
    assert response.status_code == 200 and parser.token
    with client.session_transaction() as session:
        session['_user_id'] = session_id
        session['_fresh'] = True
    return client, parser.token


@pytest.mark.parametrize('actor', [0, 1])
def test_foreign_objects_denied_for_operator_and_admin(review_case, actor):
    app, data = review_case
    client, token = client_for(app, data['ids'][actor])
    gets = [f"/positions/{data['position']}/edit", f"/positions/{data['position']}/close",
            f"/options/positions/{data['option']}/edit", f"/options/positions/{data['option']}/close",
            f"/dividends/{data['dividend']}/edit", f"/transactions/{data['transaction']}/edit",
            f"/tables/portfolios?portfolio_id={data['portfolio']}"]
    for path in gets:
        for headers in ({}, {'HX-Request': 'true'}):
            assert client.get(path, headers=headers).status_code == 404, path
    posts = [f"/positions/{data['position']}", f"/positions/{data['position']}/delete",
             f"/positions/{data['position']}/close", f"/options/positions/{data['option']}",
             f"/options/positions/{data['option']}/delete", f"/options/positions/{data['option']}/close",
             f"/dividends/{data['dividend']}", f"/dividends/{data['dividend']}/delete",
             f"/tables/portfolios/{data['portfolio']}", f"/tables/portfolios/{data['portfolio']}/delete",
             f"/tables/portfolios/{data['portfolio']}/activate",
             f"/tables/portfolios/{data['portfolio']}/tickers",
             f"/transactions/{data['transaction']}", f"/transactions/{data['transaction']}/delete"]
    for path in posts:
        response = client.post(path, data={'csrf_token': token})
        assert response.status_code == 404, (path, response.status_code)
    with app.app_context():
        assert db.session.get(Position, data['position']).quantity == 17
        assert db.session.get(OptionPosition, data['option']).quantity == 19
        assert db.session.get(Dividend, data['dividend']).amount == Decimal('123.45')
        assert db.session.get(Transaction, data['transaction']).result == 3


def test_foreign_names_absent_from_lists_and_fragments(review_case):
    app, data = review_case
    client, _ = client_for(app, data['ids'][0])
    for path in ['/', '/options', '/transactions', '/dividends', '/tables/portfolios',
                 '/performance', '/risk', '/analysis/exposure-asset', '/analysis/exposure-broker']:
        for headers in ({}, {'HX-Request': 'true'}):
            response = client.get(path, headers=headers)
            assert response.status_code == 200, (path, response.status_code)
            html = response.get_data(as_text=True)
            assert data['names'][2] not in html, path
            assert data['dividend_note'] not in html, path
            assert data['transaction_note'] not in html, path


def test_foreign_quote_is_denied_in_full_pages_and_fragments(review_case):
    app, data = review_case
    client, _ = client_for(app, data['ids'][0])
    for field in ('ticker_id', 'benchmark_ticker_id'):
        for headers in ({}, {'HX-Request': 'true'}):
            response = client.get('/quotes', query_string={field: data['ticker']}, headers=headers)
            assert response.status_code == 404


def test_malformed_filter_ids_fail_cleanly(review_case):
    app, data = review_case
    client, _ = client_for(app, data['ids'][0])
    for value in ['²', '-1', '0', '1' * 5000, '9223372036854775808']:
        response = client.get('/', query_string={'portfolio_id': value})
        assert response.status_code == 400, (value[:12], response.status_code)
    for path in ['/', '/options']:
        for value in ['²', '1' * 5000]:
            response = client.get(path, query_string={'expanded': value})
            assert response.status_code == 400, (path, value[:12], response.status_code)
    for identifier in ('9223372036854775808', '1' * 5000):
        response = client.get(f'/positions/{identifier}/edit')
        assert response.status_code in (400, 404)
    assert client.get('/dividends', query_string={'expanded_tickers': '²'}).status_code == 400


def test_operator_cannot_mutate_global_catalogue_or_prices(review_case):
    app, data = review_case
    client, token = client_for(app, data['ids'][0])
    for path in ['/quotes', '/quotes/import', '/quotes/import-position-history', '/quotes/delete-by-date',
                 '/tables/brokers', '/tables/tickers', '/tables/options/contracts', '/tables/options/expirations']:
        response = client.post(path, data={'csrf_token': token})
        assert response.status_code == 403, (path, response.status_code)


def test_create_edit_delete_preserves_quote_entitlement_without_adopting_posted_owner(review_case):
    app, data = review_case
    client, token = client_for(app, data['ids'][0])
    form = {'csrf_token': token, 'owner_id': data['user_ids'][2], 'broker_id': data['broker'],
                'ticker_id': data['ticker_ids'][0], 'portfolio_id': data['portfolio_ids'][0],
                'quantity': '4', 'average_cost': '10', 'side': 'C', 'opened_on': date.today().isoformat(),
                'target_multiplier': '1.5', 'result_mode': 'L'}
    response = client.post('/positions', data=form)
    assert response.status_code == 302, response.get_data(as_text=True)[:500]
    with app.app_context():
        position = db.session.query(Position).filter_by(owner_id=data['user_ids'][0]).one()
        identifier = position.id
        assert db.session.get(UserTickerEntitlement,(data['user_ids'][0],data['ticker_ids'][0]))
    form['ticker_id'] = data['ticker_ids'][2]
    response = client.post(f'/positions/{identifier}', data=form)
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(UserTickerEntitlement,(data['user_ids'][0],data['ticker_ids'][2]))
    response = client.post(f'/positions/{identifier}/delete', data={'csrf_token':token})
    assert response.status_code == 302
    with app.app_context():
        db.session.get(Ticker, data['ticker_ids'][2]).is_active = False
        db.session.commit()
    for ticker_id in (data['ticker_ids'][0],data['ticker_ids'][2]):
        response=client.get('/quotes',query_string={'ticker_id':ticker_id})
        assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Position,identifier) is None
        assert db.session.get(Position,data['position']).quantity == 17


def test_foreign_portfolio_cannot_be_linked_and_invalid_form_ids_are_rejected(review_case):
    app, data = review_case
    client, token = client_for(app, data['ids'][0])
    form = {'csrf_token': token, 'broker_id': data['broker'], 'ticker_id': data['ticker'],
            'portfolio_id': data['portfolio'], 'quantity': '4', 'average_cost': '10',
            'side': 'C', 'opened_on': date.today().isoformat(),
            'target_multiplier': '1.5', 'result_mode': 'L'}
    assert client.post('/positions', data=form).status_code == 422
    form['portfolio_id'] = data['portfolio_ids'][0]
    for field in ('broker_id', 'ticker_id', 'portfolio_id'):
        for invalid in ('²', '1' * 5000, '9223372036854775808'):
            assert client.post('/positions', data={**form, field: invalid}).status_code == 400
    with app.app_context():
        assert db.session.query(Position).filter_by(owner_id=data['user_ids'][0]).count() == 0
        assert db.session.query(UserTickerEntitlement).filter_by(user_id=data['user_ids'][0]).count() == 0


def test_catalogue_association_does_not_grant_quote_access(review_case):
    app, data = review_case
    client, token = client_for(app, data['ids'][0])
    response = client.post(f"/tables/portfolios/{data['portfolio_ids'][0]}/tickers",
                           data={'csrf_token': token, 'ticker_id': data['ticker']})
    assert response.status_code == 302
    assert client.get('/quotes', query_string={'ticker_id': data['ticker']}).status_code == 404
    with app.app_context():
        assert db.session.get(UserTickerEntitlement, (data['user_ids'][0], data['ticker'])) is None


def test_option_history_remains_visible_without_granting_underlying_access(review_case):
    app, data = review_case
    client, token = client_for(app, data['ids'][0])
    response = client.post('/options/positions', data={
        'csrf_token': token, 'owner_id': data['user_ids'][2], 'broker_id': data['broker'],
        'contract_id': data['contract'], 'portfolio_id': data['portfolio_ids'][0],
        'quantity': '2', 'average_cost': '1', 'target_price': '', 'side': 'C',
        'opened_on': date.today().isoformat(), 'result_mode': 'L',
    })
    assert response.status_code == 302
    with app.app_context():
        option = db.session.query(OptionPosition).filter_by(owner_id=data['user_ids'][0]).one()
        identifier = option.id
    assert client.get('/quotes', query_string={'ticker_id': data['ticker_ids'][1]}).status_code == 200
    assert client.get('/quotes', query_string={'ticker_id': data['ticker_ids'][0]}).status_code == 404
    assert client.post(f'/options/positions/{identifier}/delete', data={'csrf_token': token}).status_code == 302
    assert client.get('/quotes', query_string={'ticker_id': data['ticker_ids'][1]}).status_code == 200


@pytest.mark.parametrize('endpoint', ['/dividends', '/transactions', '/quotes', '/tables/options/contracts'])
def test_remaining_financial_and_global_form_ids_are_bounded(review_case, endpoint):
    app, data = review_case
    client, token = client_for(app, data['ids'][1])
    for invalid in ('²', '1' * 5000, '9223372036854775808'):
        response = client.post(endpoint, data={
            'csrf_token': token, 'broker_id': invalid, 'ticker_id': invalid,
        })
        assert response.status_code == 400


@pytest.mark.parametrize('endpoint,model', [('/dividends', Dividend), ('/transactions', Transaction)])
def test_edit_historical_facts_preserves_both_ticker_entitlements(review_case, endpoint, model):
    app, data = review_case
    client, token = client_for(app, data['ids'][0])
    form = {
        'csrf_token': token, 'broker_id': data['broker'], 'ticker_id': data['ticker'],
        'portfolio_id': data['portfolio_ids'][0], 'owner_id': data['user_ids'][2],
        'amount': '4', 'payment_date': date.today().isoformat(), 'quantity': '1',
        'average_cost': '10', 'exit_price': '11', 'side': 'C', 'result_mode': 'L',
        'opened_on': date.today().isoformat(), 'closed_on': date.today().isoformat(),
    }
    assert client.post(endpoint, data=form).status_code == 302
    with app.app_context():
        record_id = db.session.query(model).filter_by(owner_id=data['user_ids'][0]).one().id
    form['ticker_id'] = data['ticker_ids'][2]
    assert client.post(f'{endpoint}/{record_id}', data=form).status_code == 302
    for ticker_id in (data['ticker'], data['ticker_ids'][2]):
        assert client.get('/quotes', query_string={'ticker_id': ticker_id}).status_code == 200
