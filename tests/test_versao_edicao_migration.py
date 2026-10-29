"""Upgrade e downgrade da proteção de edição com posições existentes."""

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import text

from app import create_app, db
from tests.conftest import _url_do_banco_de_teste
from tests.test_financial_isolation_http import review_case as review_case

pytestmark = pytest.mark.banco


def test_versao_edicao_preserva_posicoes_no_downgrade_e_upgrade(review_case):
    app, data = review_case
    with app.app_context():
        db.session.remove()
    admin = create_app({"SQLALCHEMY_DATABASE_URI": _url_do_banco_de_teste(administrativo=True), "TESTING": True})
    with admin.app_context():
        try:
            downgrade(revision="20261021_0037")
            for table, key in (("positions", "position"), ("option_positions", "option")):
                assert db.session.scalar(text(f"SELECT quantity FROM {table} WHERE id=:id"), {"id": data[key]}) > 0
            db.session.commit()
            upgrade()
            for table, key in (("positions", "position"), ("option_positions", "option")):
                assert db.session.scalar(text(f"SELECT edit_version FROM {table} WHERE id=:id"), {"id": data[key]}) == 1
                db.session.execute(text(f"UPDATE {table} SET quantity=quantity+1 WHERE id=:id"), {"id": data[key]})
                assert db.session.scalar(text(f"SELECT edit_version FROM {table} WHERE id=:id"), {"id": data[key]}) == 2
            db.session.commit()
        finally:
            db.session.rollback()
            upgrade()
            db.session.remove()
