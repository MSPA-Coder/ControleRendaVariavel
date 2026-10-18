"""Execuções do myProfit: importação com ensaio e conferência contra o CB.

Nada aqui altera posições, transações ou proventos. A importação grava só em
``executions``/``execution_imports`` e a conferência é somente leitura.
"""

from __future__ import annotations

from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy.exc import IntegrityError

from app import db
from app.executions.conferencia import build_reconciliation
from app.executions.myprofit import (
    MAX_WORKBOOK_BYTES,
    ExecutionReportError,
    read_execution_report,
)
from app.executions.service import InstitutionMapError, apply_import, plan_import
from app.routes import bp
from app.routes.helpers import broker_records, current_owner_id, is_htmx_request

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
    except IntegrityError:
        db.session.rollback()
        flash("Outra importação gravou essas linhas ao mesmo tempo; refaça o ensaio.", "error")
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
    start, end = _date_arg("start"), _date_arg("end")
    result = build_reconciliation(current_owner_id(), broker=broker, start=start, end=end)
    context = {
        "result": result,
        "selected_broker": broker or "",
        "start": start.isoformat() if start else "",
        "end": end.isoformat() if end else "",
    }
    if is_htmx_request():
        return render_template("partials/execution_reconciliation_results.html", **context)
    return render_template(
        "execution_reconciliation.html", brokers=broker_records(), **context
    )
