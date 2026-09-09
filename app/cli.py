from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import click
from flask import Flask, current_app
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app import db
from app.accounts.users import UserManagementError, set_active, upsert_from_cli
from app.collector.control import CollectorStop
from app.collector.database import local_loop_arguments
from app.collector.lock import CollectorAlreadyRunningError, collector_process_lock
from app.collector.loop import run_collector_loop
from app.collector.profit_detector import WindowsProfitDetector
from app.collector.providers import CollectorProviderManager, ManagedQuoteProvider
from app.collector.rtd import ExcelRtdQuoteProvider, Instrument
from app.collector.rtd_direct import DirectRtdQuoteProvider
from app.core.domain import MARKET_TIMEZONE
from app.models import (
    ROLE_ADMIN,
    VALID_ROLES,
    CollectorMode,
    QuoteHistory,
    User,
)
from app.quotes.history_import import (
    DailyQuote,
    QuoteHistoryImportError,
    fetch_yahoo_daily_quotes,
)
from app.routes.helpers import quote_update_targets


def register_commands(app: Flask) -> None:
    app.cli.add_command(auditoria)
    app.cli.add_command(poll_rtd)
    app.cli.add_command(probe_rtd_direct)
    app.cli.add_command(import_position_history)
    app.cli.add_command(users_group)


@click.command("auditoria")
@click.option("--limite", default=50, show_default=True, help="Quantas linhas mostrar.")
@click.option("--entidade", default=None, help="Filtra por entidade (ex.: sessao, usuario).")
@click.option("--acao", default=None, help="Filtra por acao (ex.: login_recusado).")
@click.option("--usuario", default=None, help="Filtra pelo nome de quem agiu.")
def auditoria(limite: int, entidade: str | None, acao: str | None, usuario: str | None) -> None:
    """Consulta a trilha de auditoria.

    Nao ha tela para isto de proposito: a trilha e consultada raramente e sob
    suspeita, e uma tela nova seria funcionalidade nova -- que nao era o pedido.
    A CLI ja existe, ja roda dentro do conteiner e ja e como as contas sao
    provisionadas.
    """
    from sqlalchemy import select

    from app import db
    from app.models import AuditLog, User

    consulta = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if entidade:
        consulta = consulta.where(AuditLog.entity == entidade)
    if acao:
        consulta = consulta.where(AuditLog.action == acao)
    if usuario:
        consulta = consulta.join(User, AuditLog.user_id == User.id).where(User.username == usuario)

    linhas = list(db.session.scalars(consulta.limit(limite)))
    if not linhas:
        click.echo("Nenhum registro para esse filtro.")
        return

    for linha in reversed(linhas):
        autor = linha.user_ref.username if linha.user_ref else "-"
        alvo = f"{linha.entity}#{linha.entity_id}" if linha.entity_id else linha.entity
        quando = linha.created_at.astimezone(MARKET_TIMEZONE).strftime("%d/%m/%Y %H:%M:%S")
        detalhes = f"  {linha.details}" if linha.details else ""
        click.echo(f"{quando}  {autor:<16} {linha.action:<16} {alvo}{detalhes}")
    click.echo("")
    click.echo(f"{len(linhas)} registro(s).")


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
    """Coleta somente para o banco local, independentemente do agente do VPS."""
    try:
        with collector_process_lock(Path(current_app.root_path).parent, wait=False):
            _poll_rtd(watch)
    except (CollectorAlreadyRunningError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


def _collector_providers() -> CollectorProviderManager:
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

    return CollectorProviderManager(provider_factory)


def _collector_logger() -> logging.Logger:
    """Log do coletor local em stdout, que é onde a tarefa Windows o captura.

    O agente remoto escreve em arquivo próprio porque roda solto; aqui quem
    inicia o processo -- a Scheduled Task ou o supervisor -- já redireciona a
    saída, e abrir um segundo arquivo só criaria dois lugares para procurar.
    """
    logger = logging.getLogger("controle_renda_variavel.local_collector")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def _poll_rtd_once() -> None:
    """Uma leitura manual para o banco local, respeitando a agenda."""
    arguments = local_loop_arguments()
    source = arguments["source"]
    sink = arguments["sink"]
    configuration = source.configuration()  # type: ignore[attr-defined]
    if not configuration.schedule.is_active():
        return
    providers = _collector_providers()
    instruments = list(configuration.instruments)
    try:
        values = (
            providers.get(configuration.collector_mode).fetch(instruments) if instruments else []
        )
        sink.publish(values, configuration.option_keys)  # type: ignore[attr-defined]
    except Exception as exc:
        sink.report_failure(exc)  # type: ignore[attr-defined]
        raise click.ClickException(str(exc)) from exc
    finally:
        providers.close()
    click.echo(
        f"{len(values)} cotações entregues {sink.destination_label} "  # type: ignore[attr-defined]
        f"via {configuration.collector_mode.value} em {datetime.now(UTC).isoformat()}"
    )


def _poll_rtd(watch: bool) -> None:
    if not watch:
        _poll_rtd_once()
        return
    project_dir = Path(current_app.root_path).parent
    with CollectorStop(project_dir, "local") as stop:
        run_collector_loop(
            providers=_collector_providers(),
            detector=WindowsProfitDetector(),
            logger=_collector_logger(),
            should_continue=stop.running,
            sleep=stop.wait,
            stop_on_configuration_error=True,
            **local_loop_arguments(),
        )


@click.command("import-position-history")
def import_position_history() -> None:
    """Import daily action and option history from each open position's
    first date, plus every ticker registered as a comparison benchmark."""

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
    try:
        user = upsert_from_cli(username, role, password)
    except UserManagementError as erro:
        raise click.ClickException(str(erro)) from erro
    click.echo(f"Usuário '{user.username}' atualizado/criado com sucesso (papel: {user.role}).")


@users_group.command("deactivate")
@click.argument("username")
def deactivate_user(username: str) -> None:
    """Desativa um usuário (mantém o histórico, impede novos logins)."""
    user = db.session.scalar(select(User).where(User.username == username.strip()))
    if user is None:
        raise click.ClickException(f"Usuário '{username}' não encontrado.")
    try:
        set_active(user.id, False)
    except UserManagementError as erro:
        raise click.ClickException(str(erro)) from erro
    click.echo(f"Usuário '{username}' desativado.")
