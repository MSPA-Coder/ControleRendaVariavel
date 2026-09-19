from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import db


@pytest.mark.banco
def test_aplicacao_usa_papel_sem_privilegios_administrativos(app_com_banco):
    """O usuário do web grava dados, mas não pode mudar o schema."""

    with app_com_banco.app_context(), db.engine.connect() as connection:
        role, superuser, can_create_role, can_create_db = connection.execute(
            text(
                "SELECT current_user, rolsuper, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
        assert role == "investimentos_app"
        assert not superuser
        assert not can_create_role
        assert not can_create_db
        assert connection.execute(
            text("SELECT has_table_privilege(current_user, 'public.users', 'SELECT')")
        ).scalar_one()
        connection.rollback()

        try:
            with connection.begin():
                connection.execute(
                    text(
                        "CREATE TABLE public.role_permission_probe "
                        "(id integer primary key)"
                    )
                )
        except DBAPIError as error:
            assert "permission denied" in str(error).lower()
        else:
            pytest.fail("investimentos_app conseguiu criar tabela")
