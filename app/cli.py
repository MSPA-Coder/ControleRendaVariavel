from __future__ import annotations

import logging
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter

import click
from flask import Flask, current_app
from sqlalchemy import select

from app import db
from app.accounts.users import UserManagementError, set_active, upsert_from_cli
from app.collector.control import CollectorStop
from app.collector.database import local_loop_arguments
from app.collector.lock import CollectorAlreadyRunningError, collector_process_lock
from app.collector.loop import run_collector_loop
from app.collector.profit_detector import WindowsProfitDetector
from app.collector.providers import CollectorProviderManager, ManagedQuoteProvider
from app.collector.rtd import Instrument
from app.collector.rtd_direct import DirectRtdQuoteProvider
from app.core.domain import MARKET_TIMEZONE
from app.models import (
    ROLE_ADMIN,
    VALID_ROLES,
    User,
)
from app.patrimonio.cb_client import CbIncome
from app.patrimonio.outbox import DIAS_DE_RETENCAO, expurgar_outbox_v4
from app.quotes.history import quote_update_targets, upsert_quote_history
from app.quotes.history_import import (
    DailyQuote,
    QuoteHistoryImportError,
    fetch_yahoo_daily_quotes,
)


def register_commands(app: Flask) -> None:
    app.cli.add_command(auditoria)
    app.cli.add_command(poll_rtd)
    app.cli.add_command(probe_rtd_direct)
    app.cli.add_command(import_position_history)
    app.cli.add_command(expurgar_outbox)
    app.cli.add_command(importar_negocios_myprofit)
    app.cli.add_command(conferir_pregoes)
    app.cli.add_command(importar_proventos_cb)
    app.cli.add_command(substituir_proventos_planilha)
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
    def provider_factory() -> ManagedQuoteProvider:
        return DirectRtdQuoteProvider(
            prog_id=current_app.config["RTD_PROG_ID"],
            timeout_seconds=current_app.config["RTD_TIMEOUT_SECONDS"],
            refresh_seconds=min(current_app.config["RTD_REFRESH_SECONDS"], 0.25),
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
        values = providers.get().fetch(instruments) if instruments else []
        sink.publish(values, configuration.option_keys)  # type: ignore[attr-defined]
    except Exception as exc:
        sink.report_failure(exc)  # type: ignore[attr-defined]
        raise click.ClickException(str(exc)) from exc
    finally:
        providers.close()
    click.echo(
        f"{len(values)} cotações entregues {sink.destination_label} "  # type: ignore[attr-defined]
        f"via RTD direto em {datetime.now(UTC).isoformat()}"
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
@click.option(
    "--estrito",
    is_flag=True,
    help="Sai com erro se um ticker ainda detido, ou de referência, ficar sem série.",
)
def import_position_history(estrito: bool) -> None:
    """Import daily history for every ticker the portfolio ever held, over
    the period it was held, plus every comparison benchmark.

    ``--estrito`` é para quem roda sem ninguém olhando -- o timer diário do
    ``manutencao``. Sem ele, um ticker sem série só vira uma linha no stderr,
    e uma execução agendada terminaria com sucesso mesmo com o Yahoo fora.
    Com ele, o comando sai com erro, e o ``OnFailure=`` da unidade alerta.

    Só conta o ticker que ainda importa hoje: o detido e o de referência. Um
    ticker já encerrado que o Yahoo deixou de servir (ativo deslistado) tem
    a série gravada da época em que foi detido, e reprovar por ele todo dia
    ensinaria a ignorar o alerta.
    """

    started = perf_counter()
    targets = quote_update_targets()
    db.session.rollback()
    today = date.today()

    imported: list[tuple[int, DailyQuote]] = []
    failures: list[str] = []
    current_failures: list[str] = []
    for target, start_date, end_date in targets:
        try:
            quotes = fetch_yahoo_daily_quotes(target, start_date, end_date)
        except QuoteHistoryImportError:
            failures.append(target.symbol)
            if end_date == today:
                current_failures.append(target.symbol)
            continue
        imported.extend((target.id, quote) for quote in quotes)
    if imported:
        with db.session.begin():
            upsert_quote_history(
                (ticker_id, quote.price, quote.recorded_date, quote.recorded_at)
                for ticker_id, quote in imported
            )
    click.echo(f"{len(imported)} daily quotes imported for {len(targets) - len(failures)} tickers.")
    if failures:
        click.echo("No Yahoo history for: " + ", ".join(failures), err=True)
    logging.getLogger(__name__).info(
        "quote_history_batch entrypoint=cli targets=%d failures=%d records=%d duration_ms=%.1f",
        len(targets), len(failures), len(imported), (perf_counter() - started) * 1000,
    )
    if estrito and current_failures:
        raise click.ClickException(
            "Ainda detidos ou de referência, sem série: " + ", ".join(current_failures)
        )


@click.command("expurgar-outbox-v4")
@click.option(
    "--dias",
    default=DIAS_DE_RETENCAO,
    show_default=True,
    type=click.IntRange(min=1),
    help="Idade mínima, em dias, da linha que pode sair.",
)
def expurgar_outbox(dias: int) -> None:
    """Apaga do outbox do contrato v4 as linhas antigas que nenhum consumidor
    precisa mais. Ver `app/patrimonio/outbox.py` para o porquê de a linha mais
    nova de cada dono nunca sair. Roda no timer diário do ``manutencao``."""
    with db.session.begin():
        removidas = expurgar_outbox_v4(datetime.now(UTC), dias)
    click.echo(f"{removidas} linha(s) do outbox v4 com mais de {dias} dia(s) removida(s).")


def _owner_for_command(username: str | None) -> int:
    """Dono dos dados do comando: ``--usuario`` ou, na falta, ``PATRIMONIO_OWNER_ID``.

    Comando de terminal não tem sessão; o dono precisa ser dito, e nunca é
    "todos": toda consulta e gravação seguintes são filtradas por ele.
    """
    if username:
        user = db.session.scalar(select(User).where(User.username == username.strip()))
        if user is None:
            raise click.ClickException(f"Usuário '{username}' não encontrado.")
        return int(user.id)
    configured = str(current_app.config.get("PATRIMONIO_OWNER_ID") or "").strip()
    if configured.isdigit() and int(configured) > 0:
        return int(configured)
    raise click.ClickException("Informe --usuario (ou defina PATRIMONIO_OWNER_ID).")


@click.command("importar-negocios-myprofit")
@click.argument("arquivo", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--usuario", default=None, help="Dono dos dados (padrão: PATRIMONIO_OWNER_ID).")
@click.option("--gravar", is_flag=True, help="Grava de fato. Sem esta opção o comando só faz o ensaio.")
def importar_negocios_myprofit(arquivo: Path, usuario: str | None, gravar: bool) -> None:
    """Importa o relatório 'Histórico de investimentos' do myProfit (XLSX).

    O padrão é o ENSAIO: conta linhas novas, repetidas e alteradas e não grava
    nada. Com --gravar, grava só em executions/execution_imports; posições,
    transações e proventos não são tocados. O nome e o CPF do cabeçalho são
    descartados e nunca aparecem na saída.
    """
    from sqlalchemy.exc import IntegrityError

    from app.executions.myprofit import (
        MAX_WORKBOOK_BYTES,
        ExecutionReportError,
        read_execution_report,
    )
    from app.executions.service import InstitutionMapError, apply_import, plan_import

    owner_id = _owner_for_command(usuario)
    if arquivo.stat().st_size > MAX_WORKBOOK_BYTES:
        raise click.ClickException("O relatório excede o limite de 2 MB.")
    try:
        report = read_execution_report(arquivo.read_bytes())
        plan = plan_import(owner_id, report)
    except (ExecutionReportError, InstitutionMapError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        f"Período {report.period_start:%d/%m/%Y} a {report.period_end:%d/%m/%Y}; sha256 {report.sha256[:12]}."
    )
    click.echo(
        f"{len(report.rows)} linha(s) lida(s): {len(plan.new_rows)} nova(s), {plan.repeated} repetida(s), "
        f"{len(plan.changed)} alterada(s); {plan.out_of_scope} fora de escopo; "
        f"{plan.absent_from_file} gravada(s) antes e ausente(s) do arquivo."
    )
    for problem in plan.problems:
        click.echo(f"PENDÊNCIA: {problem}", err=True)
    if plan.problems:
        raise click.ClickException("O ensaio tem pendências; nada foi gravado.")
    if not gravar:
        click.echo("Ensaio: nada foi gravado. Use --gravar para confirmar.")
        return
    if not plan.can_write:
        click.echo("Nada a gravar: todas as linhas já estão importadas.")
        return
    try:
        batch = apply_import(owner_id, plan, file_name=arquivo.name)
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise click.ClickException("Conflito de gravação concorrente; refaça o ensaio.") from exc
    click.echo(
        f"Gravado o lote {batch.id}: {batch.rows_created} nova(s), {batch.rows_updated} alterada(s). "
        "Posições não foram alteradas."
    )


@click.command("conferir-pregoes")
@click.option("--usuario", default=None, help="Dono dos dados (padrão: PATRIMONIO_OWNER_ID).")
@click.option("--corretora", default=None, help="Só esta corretora (nome no CRV).")
@click.option("--inicio", type=click.DateTime(formats=["%Y-%m-%d"]), default=None)
@click.option("--fim", type=click.DateTime(formats=["%Y-%m-%d"]), default=None)
def conferir_pregoes(
    usuario: str | None, corretora: str | None, inicio: datetime | None, fim: datetime | None
) -> None:
    """Confere cada pregão: liquidação do CB contra as execuções do myProfit.

    Somente leitura. Estados: confere, diferença, só no CB, só no CRV. A
    diferença (CRV - CB) é o custo implícito do pregão.
    """
    from app.executions.conferencia import STATE_LABELS, build_reconciliation

    result = build_reconciliation(
        _owner_for_command(usuario),
        broker=corretora,
        start=inicio.date() if inicio else None,
        end=fim.date() if fim else None,
    )
    if result.cb_status != "ok":
        click.echo(f"{result.cb_message or 'CB indisponível.'} (só as execuções do CRV)", err=True)
    for row in result.rows:
        cb = f"{row.cb_amount:,.2f}" if row.cb_amount is not None else "-"
        crv = f"{row.crv_amount:,.2f}" if row.crv_amount is not None else "-"
        cost = f"{row.implicit_cost:,.2f}" if row.implicit_cost is not None else "-"
        click.echo(
            f"{row.pregao:%d/%m/%Y}  {row.broker:<10} {row.currency}  {STATE_LABELS[row.state]:<15} "
            f"CB {cb:>14}  CRV {crv:>14}  custo {cost:>10}"
        )
    counts = result.counts
    click.echo(
        f"{len(result.rows)} pregão(ões): {counts['confere']} confere, {counts['diferenca']} diferença, "
        f"{counts['so_cb']} só no CB, {counts['so_crv']} só no CRV (tolerância {result.tolerance})."
    )


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _load_cb_income() -> CbIncome:
    """Lê os lançamentos de renda do CB (somente leitura) ou falha com mensagem clara."""
    from app.executions.conferencia import cb_config_from_app
    from app.patrimonio.cb_client import CbUnavailableError, read_income_entries

    try:
        config = cb_config_from_app()
        if config is None:
            raise click.ClickException("CB não configurado (CB_PATRIMONIO_URL e CB_PATRIMONIO_TOKEN_FILE).")
        return read_income_entries(config)
    except CbUnavailableError as exc:
        raise click.ClickException(str(exc)) from exc


@click.command("importar-proventos-cb")
@click.option("--usuario", default=None, help="Dono dos dados (padrão: PATRIMONIO_OWNER_ID).")
@click.option(
    "--corte",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Data inicial (padrão: PROVENTOS_CB_CORTE).",
)
@click.option("--aplicar", is_flag=True, help="Grava de fato. Sem esta opção o comando só faz o ensaio.")
def importar_proventos_cb(usuario: str | None, corte: datetime | None, aplicar: bool) -> None:
    """Importa os proventos das corretoras em reais a partir do Controle Bancário.

    O padrão é o ENSAIO: mostra o que criaria e atualizaria, os totais e a fila
    de revisão (crédito sem ativo ou com ticker que o CRV não cadastra), e não
    grava nada. Com --aplicar grava só linhas origin='cb', de forma idempotente.
    Linhas de planilha não são tocadas (a substituição é outro comando).
    """
    from sqlalchemy.exc import IntegrityError

    from app.dividends.cb_sync import apply_cb_import, plan_cb_import

    owner_id = _owner_for_command(usuario)
    plan = plan_cb_import(owner_id, _load_cb_income(), cutoff=corte.date() if corte else None)
    result = plan.income
    click.echo(f"Corte {plan.cutoff:%d/%m/%Y}. Créditos válidos: {len(result.proventos)}.")
    click.echo(
        f"Bruto {_money(result.gross_total)}  retenções {_money(result.withheld_total)}  "
        f"líquido {_money(result.net_total)}."
    )
    click.echo(
        f"{len(plan.create)} a criar, {len(plan.update)} a atualizar, {plan.unchanged} já iguais; "
        f"{len(result.review)} na fila de revisão; {len(plan.absent_from_cb)} gravada(s) que o CB não tem mais."
    )
    click.echo(
        f"Ignorados: {result.before_cutoff} antes do corte, {result.other_institution} de outras instituições, "
        f"{result.other_currency} em outra moeda, {plan.not_realized} ainda não realizados."
    )
    for verb, rows in (("CRIAR", plan.create), ("ATUALIZAR", plan.update)):
        for prepared in rows:
            item = prepared.provento
            click.echo(
                f"  {verb:<9} {item.payment_date:%d/%m/%Y} {item.broker:<7} {item.ticker:<7} {item.kind:<9} "
                f"bruto {_money(item.gross):>10}  retido {_money(item.withheld):>9}  "
                f"líquido {_money(item.net):>10}  (CB {item.origin_id})"
            )
    if result.review:
        click.echo("FILA DE REVISÃO (nada é gravado a partir dela):", err=True)
        for review in result.review:
            click.echo(
                f"  {review.payment_date:%d/%m/%Y} CB {review.entry_id:<6} {review.instrument or '-':<7} "
                f"{review.kind or '-':<9} {_money(review.amount):>10}  {review.message}",
                err=True,
            )
    for row in plan.absent_from_cb:
        click.echo(
            f"  AUSENTE NO CB: {row.payment_date:%d/%m/%Y} {row.ticker} {_money(row.amount)} "
            f"(lançamento {row.origin_id})",
            err=True,
        )
    for problem in plan.problems:
        click.echo(f"PENDÊNCIA: {problem}", err=True)
    if plan.problems:
        raise click.ClickException("O ensaio tem pendências; nada foi gravado.")
    if not aplicar:
        click.echo("Ensaio: nada foi gravado. Use --aplicar para confirmar.")
        return
    if not plan.can_write:
        click.echo("Nada a gravar: as linhas do CB já estão no CRV.")
        return
    try:
        created, updated = apply_cb_import(owner_id, plan)
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise click.ClickException("Conflito de gravação concorrente; refaça o ensaio.") from exc
    click.echo(f"Gravado: {created} criada(s), {updated} atualizada(s). Linhas de planilha não foram alteradas.")


@click.command("substituir-proventos-planilha-2026")
@click.option("--usuario", default=None, help="Dono dos dados (padrão: PATRIMONIO_OWNER_ID).")
@click.option(
    "--corte",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Data inicial (padrão: PROVENTOS_CB_CORTE).",
)
@click.option("--aplicar", is_flag=True, help="Marca de fato as linhas pareadas como substituídas (pede confirmação).")
@click.option("--desfazer", is_flag=True, help="Devolve à planilha ativa as linhas substituídas (pede confirmação).")
@click.option("--sim", is_flag=True, help="Dispensa a pergunta de confirmação (uso em script).")
def substituir_proventos_planilha(
    usuario: str | None, corte: datetime | None, aplicar: bool, desfazer: bool, sim: bool
) -> None:
    """Marca como substituídas as linhas de planilha que o CB passou a cobrir.

    Escopo: origin='planilha', em reais, corretoras do CB, pagamento a partir do
    corte. Roda DEPOIS de importar-proventos-cb --aplicar. O ensaio (padrão)
    mostra, linha a linha, o par no CB ou a divergência; só as linhas com par
    são marcadas, nada é apagado e --desfazer reverte. Exige backup validado do
    banco e autorização do mantenedor antes de --aplicar em produção.
    """
    from app.dividends.cb_sync import (
        apply_substitution,
        plan_substitution,
        superseded_rows,
        undo_substitution,
    )

    owner_id = _owner_for_command(usuario)
    cutoff = corte.date() if corte else None
    if aplicar and desfazer:
        raise click.ClickException("Use --aplicar ou --desfazer, não os dois.")
    if desfazer:
        rows = superseded_rows(owner_id, cutoff=cutoff)
        click.echo(f"{len(rows)} linha(s) de planilha substituída(s) seriam devolvidas à planilha ativa.")
        if not rows:
            return
        if not sim:
            click.confirm("Desfazer a substituição?", abort=True)
        restored = undo_substitution(owner_id, cutoff=cutoff)
        db.session.commit()
        click.echo(f"{restored} linha(s) devolvida(s) à planilha ativa.")
        return

    plan = plan_substitution(owner_id, cutoff=cutoff)
    pairs = plan.match.pairs
    click.echo(
        f"Corte {plan.cutoff:%d/%m/%Y}. Planilha elegível: {plan.eligible} linha(s) "
        f"({plan.already_superseded} já substituída(s)). Créditos do CB já no CRV: "
        f"{len(pairs) + len(plan.match.unmatched_cb)}."
    )
    click.echo(f"Pareadas: {len(plan.match.matched_sheet_ids)} linha(s) em {len(pairs)} crédito(s) do CB.")
    for pair in pairs:
        sheet = " + ".join(f"{line.payment_date:%d/%m} {_money(line.amount)}" for line in pair.sheet)
        diff = f"  (dif {pair.difference:+})" if pair.difference else ""
        click.echo(
            f"  {pair.cb.payment_date:%d/%m/%Y} {pair.cb.broker:<7} {pair.cb.ticker:<7} {pair.cb.kind:<9} "
            f"CB bruto {_money(pair.cb.gross):>10}  esperado na planilha {_money(pair.expected):>9}  "
            f"planilha {sheet}{diff}"
        )
    for row in plan.divergences:
        click.echo(
            f"DIVERGÊNCIA (sem par no CB, não será marcada): {row.payment_date:%d/%m/%Y} {row.broker} "
            f"{row.ticker} {row.kind.value} {_money(row.amount)} (planilha {row.id})",
            err=True,
        )
    for credit in plan.match.unmatched_cb:
        click.echo(
            f"  CB sem linha na planilha: {credit.payment_date:%d/%m/%Y} {credit.broker} {credit.ticker} "
            f"{credit.kind} bruto {_money(credit.gross)}"
        )
    if not aplicar:
        click.echo("Ensaio: nada foi alterado. Use --aplicar para marcar as pareadas (depois do backup validado).")
        return
    if not pairs:
        click.echo("Nada a substituir.")
        return
    if plan.divergences:
        click.echo(f"Atenção: {len(plan.divergences)} linha(s) de planilha continuam ativas por divergência.", err=True)
    if not sim:
        click.confirm(f"Marcar {len(plan.match.matched_sheet_ids)} linha(s) de planilha como substituídas?", abort=True)
    marked = apply_substitution(owner_id, plan)
    db.session.commit()
    click.echo(f"{marked} linha(s) marcada(s) como substituída(s). Nada foi apagado; use --desfazer para reverter.")


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
