"""Execuções do myProfit: importação com ensaio, coleta, aplicação e conferência contra o CB.

A importação dos negócios e a da coleta gravam só o que veio do myProfit
(``executions``, retratos de posição e a conferência de proventos); a
conferência é somente leitura. Só "Aplicar negócios" altera posições, pelos
mesmos serviços das telas de posição.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy.exc import IntegrityError

from app import db
from app.executions import coleta, coleta_importacao
from app.executions.aplicacao import (
    ALREADY_ENTERED,
    APPLIED,
    PENDING,
    ApplicationReport,
    apply_executions,
    pending_executions,
)
from app.executions.conferencia import build_reconciliation
from app.executions.myprofit import (
    MAX_WORKBOOK_BYTES,
    ExecutionReportError,
    read_execution_report,
)
from app.executions.service import InstitutionMapError, apply_import, plan_import
from app.routes import bp
from app.routes.helpers import (
    broker_records,
    current_owner_id,
    is_htmx_request,
    selected_currency_filter,
)

#: "abriu a posição 123": o número é do banco, e não do negócio.
_POSITION_NUMBER = re.compile(r"(posição|opção) [0-9]+")
#: Quantas linhas do ensaio a tela detalha; o resto fica nas contagens.
PREVIEW_ROWS = 200


def _render_import(plan: object | None = None, status: int = 200) -> ResponseReturnValue:
    return render_template("execution_import.html", plan=plan, preview_rows=PREVIEW_ROWS), status


@bp.route("/executions/import", methods=["GET", "POST"])
def import_executions() -> ResponseReturnValue:
    """Ensaio por padrão; só grava com a confirmação sobre o mesmo arquivo."""
    if request.method == "GET":
        return _render_import()
    uploaded = request.files.get("report")
    if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith(".xlsx"):
        flash("Selecione o relatório no formato XLSX.", "error")
        return _render_import(status=422)
    content = uploaded.stream.read(MAX_WORKBOOK_BYTES + 1)
    try:
        report = read_execution_report(content)
        plan = plan_import(current_owner_id(), report)
    except (ExecutionReportError, InstitutionMapError) as exc:
        flash(str(exc), "error")
        return _render_import(status=422)

    if request.form.get("action") != "gravar":
        return _render_import(plan)
    if request.form.get("expected_sha256") != report.sha256:
        flash("O arquivo enviado não é o mesmo do ensaio; refaça o ensaio antes de gravar.", "error")
        return _render_import(plan, 422)
    if plan.problems:
        flash("O ensaio tem pendências; corrija o arquivo ou o cadastro antes de gravar.", "error")
        return _render_import(plan, 422)
    if not plan.can_write:
        flash("Nada a gravar: todas as linhas já estão importadas.", "info")
        return _render_import(plan)
    try:
        batch = apply_import(current_owner_id(), plan, file_name=uploaded.filename)
        db.session.commit()
    except (IntegrityError, ValueError):
        db.session.rollback()
        flash("As execuções mudaram durante a importação; refaça o ensaio.", "error")
        return _render_import(status=409)
    flash(
        f"Importação gravada: {batch.rows_created} nova(s), {batch.rows_updated} alterada(s), "
        f"{plan.repeated} repetida(s) ignorada(s). Posições não foram alteradas.",
        "success",
    )
    return redirect(url_for("portfolio.execution_reconciliation"))


def _date_arg(name: str) -> date | None:
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        abort(400, description="Data inválida.")


@bp.get("/executions/reconciliation")
def execution_reconciliation() -> str:
    """Liquidação do CB contra as execuções, por corretora e pregão (leitura)."""
    broker = request.args.get("broker") or None
    currency = selected_currency_filter()
    start, end = _date_arg("start"), _date_arg("end")
    result = build_reconciliation(
        current_owner_id(), broker=broker, currency=currency, start=start, end=end
    )
    context = {
        "result": result,
        "selected_broker": broker or "",
        "global_currency": currency,
        "start": start.isoformat() if start else "",
        "end": end.isoformat() if end else "",
    }
    if is_htmx_request():
        return render_template("partials/execution_reconciliation_results.html", **context)
    return render_template(
        "execution_reconciliation.html", brokers=broker_records(), **context
    )


# --- Coleta do myProfit -----------------------------------------------------------


def _render_collection(plano: object | None = None, status: int = 200) -> ResponseReturnValue:
    return (
        render_template(
            "execution_collection.html", plano=plano, ultima=coleta.ultima_coleta(current_owner_id()),
            limite_mb=coleta.LIMITE_DE_BYTES // (1024 * 1024),
        ),
        status,
    )


@bp.route("/executions/collection", methods=["GET", "POST"])
def import_collection() -> ResponseReturnValue:
    """A coleta do myProfit pela tela: o mesmo caso de uso do comando.

    Ensaio por padrão; só grava com a confirmação sobre o mesmo pacote. Negócios
    novos não chegam às posições aqui: isso é "Aplicar negócios".
    """
    if request.method == "GET":
        return _render_collection()
    uploaded = request.files.get("pacote")
    if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith(".json"):
        flash("Selecione o pacote da coleta, no formato JSON.", "error")
        return _render_collection(status=422)
    try:
        plano = coleta_importacao.planejar(current_owner_id(), uploaded.stream.read(coleta.LIMITE_DE_BYTES + 1))
    except coleta_importacao.ColetaRecusadaError as exc:
        flash(str(exc), "error")
        return _render_collection(status=422)

    if request.form.get("action") != "gravar":
        return _render_collection(plano)
    if request.form.get("expected_sha256") != plano.pacote.sha256:
        flash("O pacote enviado não é o mesmo do ensaio; refaça o ensaio antes de gravar.", "error")
        return _render_collection(plano, 422)
    if not plano.pode_gravar:
        flash(plano.impedimentos[0], "error")
        return _render_collection(plano, 422)
    try:
        coleta_importacao.gravar(current_owner_id(), plano, file_name=uploaded.filename)
        db.session.commit()
    except (IntegrityError, ValueError):
        db.session.rollback()
        flash("Os dados mudaram durante a importação; refaça o ensaio.", "error")
        return _render_collection(status=409)
    flash(
        f"Coleta de {plano.pacote.de:%d/%m/%Y} a {plano.pacote.ate:%d/%m/%Y} gravada. "
        "Negócios novos ainda não estão nas posições: use “Aplicar negócios”.",
        "success",
    )
    return redirect(url_for("portfolio.apply_pending_executions"))


# --- Aplicar negócios às posições -------------------------------------------------


def _application_fingerprint(report: ApplicationReport) -> str:
    """O que o ensaio mostrou, para a gravação só valer se fizer exatamente o mesmo.

    O número da posição aberta fica de fora: o ensaio desfeito gasta um número da
    sequência, e a gravação abre a mesma posição com o seguinte.
    """
    lines = sorted(
        f"{outcome.trade.label}|{outcome.state}|{_POSITION_NUMBER.sub(r'\1', outcome.detail)}"
        for outcome in report.outcomes
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _render_application(
    until: date | None, report: ApplicationReport | None = None, status: int = 200, *, gravado: bool = False
) -> ResponseReturnValue:
    return (
        render_template(
            "execution_application.html",
            pendentes=len(pending_executions(current_owner_id(), until=until)),
            until=until.isoformat() if until else "",
            until_date=until,
            report=report,
            fingerprint=_application_fingerprint(report) if report is not None else "",
            gravado=gravado,
            labels={APPLIED: "Aplicada", ALREADY_ENTERED: "Já lançada", PENDING: "Pendente"},
        ),
        status,
    )


@bp.route("/executions/apply", methods=["GET", "POST"])
def apply_pending_executions() -> ResponseReturnValue:
    """Leva às posições os negócios importados: o mesmo ``apply_executions`` do comando.

    O ensaio aplica de verdade e desfaz a transação, então mostra exatamente o
    que a gravação faz. Gravar exige o resultado do ensaio que a tela mostrou: se
    algo mudou no meio, nada é gravado e o ensaio novo aparece.
    """
    raw_until = (request.form if request.method == "POST" else request.args).get("until", "").strip()
    try:
        until = date.fromisoformat(raw_until) if raw_until else None
    except ValueError:
        abort(400, description="Data inválida.")
    if request.method == "GET":
        return _render_application(until)

    try:
        report = apply_executions(current_owner_id(), until=until)
    except Exception:
        db.session.rollback()
        raise
    if request.form.get("action") != "gravar":
        db.session.rollback()
        return _render_application(until, report)
    if request.form.get("expected") != _application_fingerprint(report):
        db.session.rollback()
        flash("Os negócios mudaram desde o ensaio; confira o resultado abaixo e grave de novo.", "error")
        return _render_application(until, report, 409)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("As posições mudaram durante a gravação; refaça o ensaio.", "error")
        return _render_application(until, status=409)
    flash(
        f"{len(report.of(APPLIED))} negócio(s) aplicado(s), {len(report.of(ALREADY_ENTERED))} já lançado(s), "
        f"{len(report.pending)} pendente(s).",
        "success",
    )
    return _render_application(until, report, gravado=True)
