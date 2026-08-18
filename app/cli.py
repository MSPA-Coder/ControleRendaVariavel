from __future__ import annotations

import ctypes
import os
from datetime import UTC, datetime
from decimal import Decimal

import click
from flask import Flask, current_app
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import joinedload

from app import db
from app.collector import CollectorProviderManager, ManagedQuoteProvider
from app.collector_settings import default_collector_settings
from app.domain import MARKET_TIMEZONE
from app.models import (
    ROLE_ADMIN,
    VALID_ROLES,
    AppSetting,
    CollectorMode,
    OptionContract,
    OptionPosition,
    OptionQuote,
    Position,
    Quote,
    QuoteHistory,
    User,
)
from app.quote_history_import import (
    DailyQuote,
    QuoteHistoryImportError,
    fetch_yahoo_daily_quotes,
)
from app.routes.helpers import quote_update_targets, upsert_quote_history
from app.rtd import ExcelRtdQuoteProvider, Instrument
from app.rtd_direct import DirectRtdQuoteProvider


def register_commands(app: Flask) -> None:
    app.cli.add_command(poll_rtd)
    app.cli.add_command(probe_rtd_direct)
    app.cli.add_command(import_position_history)
    app.cli.add_command(users_group)


def _windows_process_is_alive(process_id: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.OpenProcess(process_query_limited_information, False, process_id)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def supervisor_process_is_alive(supervisor_pid: str | None) -> bool:
    """Mantém o coletor apenas enquanto seu controlador ainda existir."""
    if not supervisor_pid:
        return True
    try:
        process_id = int(supervisor_pid)
    except ValueError:
        return False
    if process_id <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_alive(process_id)
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True


@click.command("probe-rtd-direct")
@click.option("--ticker", required=True, help="Ticker a consultar, sem registrar no banco.")
@click.option("--market-code", required=True, type=click.Choice(["B", "Y", "N"]))
def probe_rtd_direct(ticker: str, market_code: str) -> None:
    """Tests the ProfitPro IRTDServer contract without starting Excel."""

    instrument = Instrument(1, ticker.strip().upper(), market_code)
    provider = DirectRtdQuoteProvider(
        prog_id=current_app.config["RTD_PROG_ID"],
        timeout_seconds=current_app.config["RTD_TIMEOUT_SECONDS"],
    )
    try:
        with provider:
            values = provider.fetch([instrument])
    except Exception as exc:
        raise click.ClickException(f"RTD direto indisponível: {exc}") from exc
    click.echo(
        f"RTD direto respondeu para {instrument.ticker}: "
        f"{len(values)} instrumento, campos ULT/FEC/EST válidos."
    )


@click.command("poll-rtd")
@click.option("--watch", is_flag=True, help="Continua atualizando até ser interrompido.")
def poll_rtd(watch: bool) -> None:
    """Refreshes quotes with the collector selected on the Settings page."""
    import time

    def provider_factory(mode: CollectorMode) -> ManagedQuoteProvider:
        common = {
            "prog_id": current_app.config["RTD_PROG_ID"],
            "timeout_seconds": current_app.config["RTD_TIMEOUT_SECONDS"],
        }
        if mode == CollectorMode.DIRECT:
            return DirectRtdQuoteProvider(
                **common,
                refresh_seconds=min(current_app.config["RTD_REFRESH_SECONDS"], 0.25),
            )
        return ExcelRtdQuoteProvider(
            **common,
            refresh_seconds=current_app.config["RTD_REFRESH_SECONDS"],
            visible=current_app.config["RTD_EXCEL_VISIBLE"],
        )

    providers = CollectorProviderManager(provider_factory)
    supervisor_pid = os.getenv("RTD_SUPERVISOR_PID")
    try:
        while True:
            if not supervisor_process_is_alive(supervisor_pid):
                return
            db.session.expire_all()
            settings = db.session.get(AppSetting, 1)
            if settings is None:
                settings = default_collector_settings()
                db.session.add(settings)
                db.session.commit()
            collector_mode = settings.collector_mode
            poll_interval_seconds = settings.poll_interval_seconds
            positions = db.session.scalars(select(Position).order_by(Position.id)).all()
            option_positions = db.session.scalars(
                select(OptionPosition)
                .options(
                    joinedload(OptionPosition.contract).joinedload(
                        OptionContract.ticker_ref
                    ),
                    joinedload(OptionPosition.contract).joinedload(
                        OptionContract.underlying_ticker_ref
                    ),
                )
                .order_by(OptionPosition.id)
            ).unique().all()
            instruments = [
                Instrument(item.id, item.ticker, item.rtd_market_code, item.side.value)
                for item in positions
            ]
            for item in option_positions:
                option_key = -item.id * 2
                instruments.extend(
                    [
                        Instrument(
                            option_key,
                            item.contract.ticker_ref.symbol,
                            item.contract.ticker_ref.rtd_market_code,
                        ),
                        Instrument(
                            option_key - 1,
                            item.contract.underlying_ticker_ref.symbol,
                            item.contract.underlying_ticker_ref.rtd_market_code,
                        ),
                    ]
                )
            db.session.rollback()
            try:
                provider = providers.get(collector_mode)
                values = provider.fetch(instruments)
                positions_by_id = {item.id: item for item in positions}
                ticker_prices: dict[int, tuple[Decimal, datetime]] = {}
                for value in (item for item in values if item.position_id > 0):
                    statement = insert(Quote).values(
                        position_id=value.position_id,
                        last_price=value.last_price,
                        previous_close=value.previous_close,
                        instrument_status=value.instrument_status,
                        source_status="online",
                        error_message=None,
                        observed_at=value.observed_at,
                    )
                    statement = statement.on_conflict_do_update(
                        index_elements=[Quote.position_id],
                        set_={
                            "last_price": statement.excluded.last_price,
                            "previous_close": statement.excluded.previous_close,
                            "instrument_status": statement.excluded.instrument_status,
                            "source_status": "online",
                            "error_message": None,
                            "observed_at": statement.excluded.observed_at,
                        },
                    )
                    db.session.execute(statement)
                    stock_position = positions_by_id.get(value.position_id)
                    if stock_position is not None:
                        ticker_prices[stock_position.ticker_id] = (
                            value.quote_history_price,
                            value.observed_at,
                        )
                values_by_id = {item.position_id: item for item in values}
                for option_position in option_positions:
                    option_key = -option_position.id * 2
                    option_value = values_by_id[option_key]
                    underlying_value = values_by_id[option_key - 1]
                    option_statement = insert(OptionQuote).values(
                        option_position_id=option_position.id,
                        last_price=option_value.last_price,
                        previous_close=option_value.previous_close,
                        underlying_price=underlying_value.last_price,
                        instrument_status=option_value.instrument_status,
                        source_status="online",
                        error_message=None,
                        observed_at=option_value.observed_at,
                    )
                    option_statement = option_statement.on_conflict_do_update(
                        index_elements=[OptionQuote.option_position_id],
                        set_={
                            "last_price": option_statement.excluded.last_price,
                            "previous_close": option_statement.excluded.previous_close,
                            "underlying_price": option_statement.excluded.underlying_price,
                            "instrument_status": option_statement.excluded.instrument_status,
                            "source_status": "online",
                            "error_message": None,
                            "observed_at": option_statement.excluded.observed_at,
                        },
                    )
                    db.session.execute(option_statement)
                    ticker_prices[option_position.contract.ticker_id] = (
                        option_value.quote_history_price,
                        option_value.observed_at,
                    )
                    ticker_prices[option_position.contract.underlying_ticker_id] = (
                        underlying_value.quote_history_price,
                        underlying_value.observed_at,
                    )
                # Um snapshot por ticker por dia, não a cada poll — ver a
                # docstring de QuoteHistory para o porquê.
                upsert_quote_history(
                    (
                        ticker_id,
                        price,
                        observed_at.astimezone(MARKET_TIMEZONE).date(),
                        observed_at,
                    )
                    for ticker_id, (price, observed_at) in ticker_prices.items()
                )
                db.session.commit()
                click.echo(
                    f"{len(values)} cotações atualizadas via {collector_mode.value} "
                    f"em {datetime.now(UTC).isoformat()}"
                )
            except Exception as exc:
                db.session.rollback()
                db.session.query(Quote).update(
                    {"source_status": "error", "error_message": str(exc)[:250]}
                )
                db.session.query(OptionQuote).update(
                    {"source_status": "error", "error_message": str(exc)[:250]}
                )
                db.session.commit()
                if not watch:
                    raise click.ClickException(str(exc)) from exc
                click.echo(f"Falha transitória no RTD: {exc}", err=True)
                time.sleep(poll_interval_seconds)
                continue
            if not watch:
                return
            time.sleep(poll_interval_seconds)
    finally:
        providers.close()


@click.command("import-position-history")
def import_position_history() -> None:
    """Import daily stock history from each open position's first date,
    plus every ticker registered as a comparison benchmark."""

    targets = quote_update_targets()
    db.session.rollback()

    imported: list[tuple[int, DailyQuote]] = []
    failures: list[str] = []
    for target, start_date in targets:
        try:
            quotes = fetch_yahoo_daily_quotes(target, start_date, datetime.now(UTC).date())
        except QuoteHistoryImportError:
            failures.append(target.symbol)
            continue
        imported.extend((target.id, quote) for quote in quotes)
    if imported:
        with db.session.begin():
            for ticker_id, quote in imported:
                statement = insert(QuoteHistory).values(
                    ticker_id=ticker_id,
                    price=quote.price,
                    recorded_date=quote.recorded_date,
                    recorded_at=quote.recorded_at,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[QuoteHistory.ticker_id, QuoteHistory.recorded_date],
                    set_={
                        "price": statement.excluded.price,
                        "recorded_at": statement.excluded.recorded_at,
                    },
                )
                db.session.execute(statement)
    click.echo(f"{len(imported)} daily quotes imported for {len(targets) - len(failures)} tickers.")
    if failures:
        click.echo("No Yahoo history for: " + ", ".join(failures), err=True)



@click.group("users")
def users_group() -> None:
    """Gerenciamento de contas de usuário (autenticação da aplicação)."""


@users_group.command("create-admin")
@click.option("--username", prompt=True, help="Nome de usuário para login.")
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Senha do usuário (solicitada de forma oculta se omitida).",
)
@click.option(
    "--role",
    type=click.Choice(sorted(VALID_ROLES)),
    default=ROLE_ADMIN,
    show_default=True,
    help="Papel: admin altera configurações; operador só opera a carteira.",
)
def create_admin(username: str, password: str, role: str) -> None:
    """Cria um usuário ou redefine sua senha e papel se já existir.

    Uso: flask users create-admin
    (username/password também podem vir por --username/--password, útil em
    scripts de provisionamento não interativos; evite deixar a senha em
    histórico de shell nesse caso.)
    """
    username = username.strip()
    if not username:
        raise click.ClickException("Informe um nome de usuário.")
    if len(password) < 8:
        raise click.ClickException("A senha deve ter ao menos 8 caracteres.")

    user = db.session.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(username=username)
        db.session.add(user)
        action = "criado"
    else:
        action = "atualizado"
    user.set_password(password)
    user.is_active_user = True
    user.role = role
    db.session.commit()
    click.echo(f"Usuário '{username}' {action} com sucesso (papel: {role}).")


@users_group.command("deactivate")
@click.argument("username")
def deactivate_user(username: str) -> None:
    """Desativa um usuário (mantém o histórico, impede novos logins)."""
    user = db.session.scalar(select(User).where(User.username == username.strip()))
    if user is None:
        raise click.ClickException(f"Usuário '{username}' não encontrado.")
    user.is_active_user = False
    db.session.commit()
    click.echo(f"Usuário '{username}' desativado.")
