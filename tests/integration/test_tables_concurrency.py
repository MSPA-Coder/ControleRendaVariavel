from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import db
from app.models import Broker

pytestmark = [pytest.mark.critical, pytest.mark.business_rule]


def test_create_broker_survives_a_concurrent_duplicate_commit(
    app: Flask, auth_client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two requests can both pass the uniqueness pre-check in
    app.routes.tables.create_broker before either commits. The UNIQUE
    constraint on Broker.name/acronym is the real guard in that race; the
    route must turn the loser's IntegrityError into a graceful flash +
    redirect instead of an uncaught 500.

    The race is reproduced deterministically instead of with real threads:
    the "winning" concurrent request's row is seeded directly through the
    session (bypassing the route entirely), then this request's own
    duplicate-check query is forced to report "nothing found" for its one
    call -- exactly what it would have seen an instant earlier, before the
    other transaction committed. The subsequent db.session.add + commit
    then collides with the UNIQUE constraint for real.
    """
    with app.app_context():
        db.session.add(Broker(name="Genial Investimentos", acronym="GENI"))
        db.session.commit()

    def scalar_missing_the_race(*args: object, **kwargs: object) -> None:
        # Only the pre-check's single call is affected: once used, stop
        # shadowing so every other query (rendering the redirected page,
        # etc.) goes through the real, per-request session as usual.
        monkeypatch.delattr(db.session, "scalar", raising=False)
        return None

    monkeypatch.setattr(db.session, "scalar", scalar_missing_the_race)

    response = auth_client.post(
        "/tables/brokers",
        data={"name": "Genial Investimentos", "acronym": "GENI"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "já está cadastrado" in response.get_data(as_text=True)

    with app.app_context():
        assert db.session.query(Broker).filter_by(name="Genial Investimentos").count() == 1
