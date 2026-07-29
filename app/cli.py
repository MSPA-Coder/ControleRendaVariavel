from __future__ import annotations

from datetime import UTC, datetime

import click
from flask import Flask, current_app
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import joinedload

from app import db
from app.collector import CollectorProviderManager, ManagedQuoteProvider
from app.collector_settings import default_collector_settings
from app.models import (
    AppSetting,
    CollectorMode,
    OptionContract,
    OptionPosition,
    OptionQuote,
    Position,
    Quote,
    User,
)
from app.rtd import ExcelRtdQuoteProvider, Instrument
from app.rtd_direct import DirectRtdQuoteProvider


def register_commands(app: Flask) -> None:
    app.cli.add_command(poll_rtd)
    app.cli.add_command(probe_rtd_direct)
    app.cli.add_command(users_group)


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
    try:
        while True:
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
                Instrument(item.id, item.ticker, item.rtd_market_code) for item in positions
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
def create_admin(username: str, password: str) -> None:
    """Cria um usuário administrador ou redefine sua senha se já existir.

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
    db.session.commit()
    click.echo(f"Usuário '{username}' {action} com sucesso.")


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
