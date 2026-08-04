from __future__ import annotations

import pytest
from flask import Flask

from app import db
from app.models import User

pytestmark = [pytest.mark.critical, pytest.mark.security]


def test_create_admin_creates_new_user(app: Flask) -> None:
    runner = app.test_cli_runner()

    result = runner.invoke(
        args=["users", "create-admin", "--username", "admin", "--password", "supersecret1"]
    )

    assert result.exit_code == 0, result.output
    assert "criado" in result.output
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "admin"))
        assert user is not None
        assert user.check_password("supersecret1")


def test_create_admin_resets_password_for_existing_user(app: Flask) -> None:
    runner = app.test_cli_runner()
    runner.invoke(
        args=["users", "create-admin", "--username", "admin", "--password", "supersecret1"]
    )

    result = runner.invoke(
        args=["users", "create-admin", "--username", "admin", "--password", "anotherpass1"]
    )

    assert result.exit_code == 0, result.output
    assert "atualizado" in result.output
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "admin"))
        assert user is not None
        assert user.check_password("anotherpass1")
        assert not user.check_password("supersecret1")


def test_create_admin_rejects_short_password(app: Flask) -> None:
    runner = app.test_cli_runner()

    result = runner.invoke(
        args=["users", "create-admin", "--username", "admin", "--password", "short"]
    )

    assert result.exit_code != 0


def test_deactivate_user(app: Flask) -> None:
    runner = app.test_cli_runner()
    runner.invoke(
        args=["users", "create-admin", "--username", "admin", "--password", "supersecret1"]
    )

    result = runner.invoke(args=["users", "deactivate", "admin"])

    assert result.exit_code == 0, result.output
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "admin"))
        assert user is not None
        assert user.is_active_user is False


def test_deactivate_unknown_user_fails(app: Flask) -> None:
    runner = app.test_cli_runner()

    result = runner.invoke(args=["users", "deactivate", "ghost"])

    assert result.exit_code != 0
