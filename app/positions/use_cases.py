"""Coordenação financeira sem HTTP: um comando, uma transação completa.

O chamador fornece o dono autenticado. Nenhuma função recebe request/form ou
importa rotas; confirmação e replay são resultados de negócio explícitos.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app import db
from app.accounts.entitlements import grant_ticker_entitlement
from app.core.submissions import claim_submission
from app.executions.locking import lock_executions
from app.models import OptionPosition, Portfolio, Position, Transaction
from app.options import closure as options
from app.positions import closure as stocks
from app.positions.commands import OptionPositionInput, PositionInput


class SubmissionConflictError(ValueError):
    """Chave ausente, inválida ou reutilizada para outro conteúdo."""


class ConfirmationRequiredError(ValueError):
    def __init__(self, *, prior_opening_date: date | None = None):
        super().__init__("O aporte exige confirmação.")
        self.prior_opening_date = prior_opening_date


@dataclass(frozen=True, slots=True)
class SubmissionResult[P]:
    position: P | None
    merged: bool = False
    prior_opening_date: date | None = None

    @property
    def replayed(self) -> bool:
        return self.position is None


@contextmanager
def _transaction() -> Iterator[None]:
    """Inclui leituras já iniciadas pelo parse; nunca faz commit parcial."""
    try:
        yield
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def _claim(owner_id: int, command: str, token: str, payload: dict[str, object]) -> bool:
    try:
        return claim_submission(owner_id=owner_id, command=command, token=token, payload=payload)
    except ValueError as exc:
        raise SubmissionConflictError(str(exc)) from exc


def _require_portfolio(owner_id: int, portfolio_id: int) -> None:
    # O caso de uso também é seguro fora do formulário que valida a carteira.
    if db.session.scalar(select(Portfolio.id).where(
        Portfolio.id == portfolio_id, Portfolio.owner_id == owner_id
    )) is None:
        raise ValueError("Selecione uma carteira cadastrada.")


def register_stock(
    data: PositionInput, *, owner_id: int, token: str,
    confirm_duplicate: bool = False, confirm_prior_opening: bool = False,
) -> SubmissionResult[Position]:
    with _transaction():
        lock_executions(owner_id)
        _require_portfolio(owner_id, data.portfolio_id)
        if not _claim(owner_id, "stock", token, asdict(data)):
            return SubmissionResult(None)
        candidate = Position(owner_id=owner_id, **asdict(data))
        # A decisão de exigir confirmação precisa observar aportes anteriores
        # depois de adquirir a mesma trava que serializa a gravação da chave.
        stocks._mergeable_position(candidate)
        earlier = stocks.prior_opening(candidate)
        earlier_date = earlier.opened_on if earlier is not None else None
        if earlier_date is not None and not confirm_prior_opening:
            raise ConfirmationRequiredError(prior_opening_date=earlier_date)
        if not confirm_duplicate and stocks.duplicate_entry(candidate) is not None:
            raise ConfirmationRequiredError()
        position, merged = stocks.create_or_merge_position(candidate, confirm_prior_opening=confirm_prior_opening)
        grant_ticker_entitlement(user_id=owner_id, ticker_id=position.ticker_id, held_on=position.opened_on)
        result = SubmissionResult(position, merged, earlier_date)
    return result


def register_option(
    data: OptionPositionInput, *, owner_id: int, token: str, confirm_duplicate: bool = False,
) -> SubmissionResult[OptionPosition]:
    with _transaction():
        lock_executions(owner_id)
        _require_portfolio(owner_id, data.portfolio_id)
        if not _claim(owner_id, "option", token, asdict(data)):
            return SubmissionResult(None)
        candidate = OptionPosition(owner_id=owner_id, **asdict(data))
        options._mergeable_position(candidate)
        if not confirm_duplicate and options.duplicate_entry(candidate) is not None:
            raise ConfirmationRequiredError()
        position, merged = options.create_or_merge_position(candidate)
        grant_ticker_entitlement(user_id=owner_id, ticker_id=position.contract.ticker_id, held_on=position.opened_on)
        result = SubmissionResult(position, merged)
    return result


def close_stock(
    position_id: int, exit_price: Decimal, closed_on: date,
    quantity: Decimal | None = None, *, owner_id: int,
) -> Transaction | None:
    with _transaction():
        lock_executions(owner_id)
        return stocks.apply_position_closure(position_id, exit_price, closed_on, quantity, owner_id=owner_id)


def close_option(
    position_id: int, exit_price: Decimal, closed_on: date,
    quantity: Decimal | None = None, *, owner_id: int,
) -> Transaction | None:
    with _transaction():
        lock_executions(owner_id)
        return options.apply_position_closure(position_id, exit_price, closed_on, quantity, owner_id=owner_id)
