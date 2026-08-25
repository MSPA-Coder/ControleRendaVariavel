"""Casos de uso administrativos para contas da aplicação."""

from __future__ import annotations

from dataclasses import dataclass

from sharedauth.passwords import validar_tamanho
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import ROLE_ADMIN, VALID_ROLES, User

_ADMIN_MUTATION_LOCK = "controle-renda-variavel:user-management"


class UserManagementError(ValueError):
    """Erro seguro para exibição ao administrador."""


class UserAlreadyExistsError(UserManagementError):
    pass


class UserNotFoundError(UserManagementError):
    pass


class LastAdminError(UserManagementError):
    pass


@dataclass(frozen=True)
class UserInput:
    username: str
    role: str


def _username(value: str) -> str:
    username = value.strip()
    if not username:
        raise UserManagementError("Informe um nome de usuário.")
    if len(username) > 80:
        raise UserManagementError("O nome de usuário deve ter no máximo 80 caracteres.")
    return username


def _role(value: str) -> str:
    role = value.strip()
    if role not in VALID_ROLES:
        raise UserManagementError("Selecione um papel válido.")
    return role


def _password(password: str, confirmation: str) -> None:
    try:
        validar_tamanho(password)
    except ValueError as exc:
        raise UserManagementError(str(exc)) from exc
    if password != confirmation:
        raise UserManagementError("A confirmação da senha não confere.")


def _lock_admin_mutations() -> None:
    """Serializa mudanças de papel/estado para preservar o último admin."""
    db.session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": _ADMIN_MUTATION_LOCK},
    )


def _active_admin_count() -> int:
    return int(
        db.session.scalar(
            select(func.count()).select_from(User).where(
                User.is_active_user.is_(True), User.role == ROLE_ADMIN
            )
        )
        or 0
    )


def _locked_user(user_id: int) -> User:
    user = db.session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise UserNotFoundError("Usuário não encontrado.")
    return user


def _commit() -> None:
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise UserAlreadyExistsError("Esse nome de usuário já está cadastrado.") from exc


def list_users() -> list[User]:
    return list(db.session.scalars(select(User).order_by(User.username)))


def create_user(username: str, role: str, password: str, confirmation: str) -> User:
    data = UserInput(_username(username), _role(role))
    _password(password, confirmation)
    _lock_admin_mutations()
    if db.session.scalar(select(User).where(User.username == data.username)) is not None:
        raise UserAlreadyExistsError("Esse nome de usuário já está cadastrado.")
    if data.role != ROLE_ADMIN and _active_admin_count() == 0:
        raise LastAdminError("Crie pelo menos um administrador ativo antes de operadores.")
    user = User(username=data.username, role=data.role, is_active_user=True)
    user.set_password(password)
    db.session.add(user)
    _commit()
    return user


def upsert_from_cli(username: str, role: str, password: str) -> User:
    """Cria ou atualiza uma conta para o bootstrap administrativo da CLI."""
    data = UserInput(_username(username), _role(role))
    _password(password, password)
    _lock_admin_mutations()
    user = db.session.scalar(select(User).where(User.username == data.username))
    if user is None:
        if data.role != ROLE_ADMIN and _active_admin_count() == 0:
            raise LastAdminError("O primeiro usuário deve ser um administrador.")
        user = User(username=data.username)
        db.session.add(user)
    else:
        user = db.session.scalar(select(User).where(User.id == user.id).with_for_update())
        if user is None:  # pragma: no cover - protegido pelo advisory lock
            raise UserNotFoundError("Usuário não encontrado.")
        if user.role == ROLE_ADMIN and data.role != ROLE_ADMIN and (
            (user.is_active_user and _active_admin_count() <= 1)
            or (not user.is_active_user and _active_admin_count() == 0)
        ):
            raise LastAdminError("Não é possível rebaixar o último administrador ativo.")
    user.set_password(password)
    user.is_active_user = True
    user.role = data.role
    _commit()
    return user


def update_user(user_id: int, username: str, role: str) -> User:
    data = UserInput(_username(username), _role(role))
    _lock_admin_mutations()
    user = _locked_user(user_id)
    duplicate = db.session.scalar(
        select(User).where(User.username == data.username, User.id != user_id)
    )
    if duplicate is not None:
        raise UserAlreadyExistsError("Esse nome de usuário já está cadastrado.")
    if user.role == ROLE_ADMIN and data.role != ROLE_ADMIN and (
        (user.is_active_user and _active_admin_count() <= 1)
        or (not user.is_active_user and _active_admin_count() == 0)
    ):
        raise LastAdminError("Não é possível rebaixar o último administrador ativo.")
    user.username = data.username
    user.role = data.role
    _commit()
    return user


def reset_password(user_id: int, password: str, confirmation: str) -> User:
    _password(password, confirmation)
    _lock_admin_mutations()
    user = _locked_user(user_id)
    user.set_password(password)
    _commit()
    return user


def set_active(user_id: int, active: bool) -> User:
    _lock_admin_mutations()
    user = _locked_user(user_id)
    if not active and user.is_active_user and user.role == ROLE_ADMIN and _active_admin_count() <= 1:
        raise LastAdminError("Não é possível desativar o último administrador ativo.")
    user.is_active_user = active
    _commit()
    return user

