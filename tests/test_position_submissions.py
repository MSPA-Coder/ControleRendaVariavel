"""Uma intenção de aporte tem um efeito, inclusive entre requisições concorrentes.

O teste HTTP real exige sessão/CSRF e PostgreSQL. Duas chaves diferentes ainda
representam aportes intencionais; unicidade da posição não substitui a chave.
"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from html.parser import HTMLParser
from threading import Barrier
from uuid import uuid4

import pytest

from app import db
from app.models import OptionPosition, Position, PositionSubmission
from tests.test_financial_isolation_http import client_for
from tests.test_financial_isolation_http import review_case as review_case

pytestmark = pytest.mark.banco


class HiddenFields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("type") == "hidden":
            self.fields[values.get("name")] = values.get("value")


def _form(data, csrf, instrument, *, owner=0, key=None):
    fields = {
        "csrf_token": csrf, "submit_token": key or uuid4().hex,
        "broker_id": data["broker"], "portfolio_id": data["portfolio_ids"][owner],
        "quantity": "100", "average_cost": "10", "side": "C",
        "opened_on": "2026-06-10", "confirm_duplicate": "1",
    }
    if instrument == "stock":
        fields.update(ticker_id=data["ticker_ids"][0], target_multiplier="1.5")
    else:
        fields["contract_id"] = data["contract"]
    return fields


def _path(instrument):
    return "/positions" if instrument == "stock" else "/options/positions"


def _quantity(app, data, instrument, owner=0):
    model = Position if instrument == "stock" else OptionPosition
    with app.app_context():
        position = db.session.query(model).filter_by(
            owner_id=data["user_ids"][owner], portfolio_id=data["portfolio_ids"][owner]
        ).one()
        return position.quantity, len(position.movements)


@pytest.mark.parametrize("instrument", ["stock", "option"])
@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_submissions_follow_intent_identity(review_case, instrument, same_key):
    app, data = review_case
    first, csrf = client_for(app, data["ids"][0])
    second = app.test_client()
    name = app.config["SESSION_COOKIE_NAME"]
    second.set_cookie(name, first.get_cookie(name).value)
    second.environ_base["REMOTE_ADDR"] = first.environ_base["REMOTE_ADDR"]
    fields = _form(data, csrf, instrument)
    other = fields if same_key else {**fields, "submit_token": uuid4().hex}
    ready = Barrier(2, timeout=15)

    def post(client, payload):
        ready.wait()
        return client.post(_path(instrument), data=payload).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(post, first, fields)
        b = executor.submit(post, second, other)
        assert [a.result(timeout=30), b.result(timeout=30)] == [302, 302]
    count = 1 if same_key else 2
    assert _quantity(app, data, instrument) == (Decimal(100 * count), count)


@pytest.mark.parametrize("instrument", ["stock", "option"])
def test_replay_keeps_quantity_and_conflicting_payload_is_rejected(review_case, instrument):
    app, data = review_case
    client, csrf = client_for(app, data["ids"][0])
    fields = _form(data, csrf, instrument)
    assert client.post(_path(instrument), data=fields).status_code == 302
    assert client.post(_path(instrument), data=fields).status_code == 302
    assert _quantity(app, data, instrument) == (Decimal(100), 1)
    conflict = client.post(_path(instrument), data={**fields, "quantity": "200"})
    assert conflict.status_code == 409
    assert _quantity(app, data, instrument) == (Decimal(100), 1)


@pytest.mark.parametrize("instrument", ["stock", "option"])
def test_confirmation_does_not_consume_submission_and_missing_key_is_rejected(review_case, instrument):
    app, data = review_case
    client, csrf = client_for(app, data["ids"][0])
    fields = _form(data, csrf, instrument)
    assert client.post(_path(instrument), data=fields).status_code == 302
    next_intent = {**fields, "submit_token": uuid4().hex, "confirm_duplicate": ""}
    assert client.post(_path(instrument), data=next_intent).status_code == 409
    with app.app_context():
        assert db.session.get(PositionSubmission, (
            data["user_ids"][0], instrument, next_intent["submit_token"]
        )) is None
    assert client.post(_path(instrument), data={**next_intent, "confirm_duplicate": "1"}).status_code == 302
    assert client.post(_path(instrument), data={**fields, "submit_token": ""}).status_code == 409
    assert _quantity(app, data, instrument) == (Decimal(200), 2)


@pytest.mark.parametrize("instrument", ["stock", "option"])
def test_submission_identity_is_scoped_to_owner(review_case, instrument):
    app, data = review_case
    shared_key = uuid4().hex
    for owner in (0, 1):
        client, csrf = client_for(app, data["ids"][owner])
        assert client.post(_path(instrument), data=_form(
            data, csrf, instrument, owner=owner, key=shared_key
        )).status_code == 302
    assert _quantity(app, data, instrument, 0) == (Decimal(100), 1)
    assert _quantity(app, data, instrument, 1) == (Decimal(100), 1)


@pytest.mark.parametrize("path", ["/positions/new", "/options/new"])
def test_new_form_has_a_new_submission_key(review_case, path):
    app, data = review_case
    client, _ = client_for(app, data["ids"][0])
    tokens = []
    for _ in range(2):
        response = client.get(path)
        assert response.status_code == 200
        parser = HiddenFields()
        parser.feed(response.get_data(as_text=True))
        tokens.append(parser.fields["submit_token"])
    assert len(tokens[0]) >= 32 and tokens[0] != tokens[1]
