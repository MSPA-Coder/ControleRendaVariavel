"""Ensaio e gravação da importação de execuções do myProfit.

O ensaio (``plan_import``) só lê: diz quantas linhas são novas, repetidas ou
alteradas e o que impede a gravação. A gravação (``apply_import``) segue o plano
e **não faz commit**: a transação é de quem chama (rota ou comando).

Esta importação não toca posições, movimentos, transações nem proventos; ela só
alimenta a tabela ``executions``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from flask import current_app
from sqlalchemy import select

from app import db
from app.executions.myprofit import ExecutionReport, ExecutionRow, safe_file_name
from app.models import Broker, Execution, ExecutionImport

#: Instituição do relatório (sem caixa) -> nome da corretora no CRV. Uma
#: instituição fora da tabela impede a gravação: melhor parar e mapear do que
#: gravar a execução na corretora errada. ``MYPROFIT_INSTITUICOES`` (JSON no
#: ambiente) substitui a tabela inteira.
DEFAULT_INSTITUTION_BROKERS = {
    "genial": "Genial",
    "xp": "XP",
    "avenue": "Avenue",
    "nomad": "Nomad",
    "revolut": "Revolut",
}

_EXECUTION_FIELDS = (
    "trade_date",
    "institution",
    "document_type",
    "asset",
    "asset_group",
    "side",
    "cash_operation",
    "quantity",
    "price_without_fees",
    "price_with_fees",
    "fees_total",
    "total_without_fees",
    "total_with_fees",
    "day_trade",
    "currency",
    "in_scope",
    "content_hash",
)


class InstitutionMapError(ValueError):
    """``MYPROFIT_INSTITUICOES`` não é um objeto JSON de texto para texto."""


def institution_brokers(config: Mapping[str, object] | None = None) -> dict[str, str]:
    raw = (config if config is not None else current_app.config).get("MYPROFIT_INSTITUICOES")
    if not raw:
        return dict(DEFAULT_INSTITUTION_BROKERS)
    try:
        loaded = json.loads(str(raw))
    except ValueError as exc:
        raise InstitutionMapError("MYPROFIT_INSTITUICOES não é JSON válido.") from exc
    if not isinstance(loaded, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in loaded.items()
    ):
        raise InstitutionMapError("MYPROFIT_INSTITUICOES deve mapear instituição para corretora.")
    return {key.strip().casefold(): value.strip() for key, value in loaded.items()}


@dataclass(slots=True)
class ImportPlan:
    report: ExecutionReport
    broker_ids: dict[str, int] = field(default_factory=dict)
    new_rows: list[ExecutionRow] = field(default_factory=list)
    changed: list[tuple[Execution, ExecutionRow]] = field(default_factory=list)
    repeated: int = 0
    #: Linhas já gravadas nos documentos do arquivo que o arquivo não traz mais.
    #: Só informativo: a importação nunca apaga.
    absent_from_file: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def can_write(self) -> bool:
        return not self.problems and bool(self.new_rows or self.changed)

    @property
    def out_of_scope(self) -> int:
        return sum(1 for row in self.report.rows if not row.in_scope)


def plan_import(owner_id: int, report: ExecutionReport) -> ImportPlan:
    """Compara o arquivo com o que o dono já tem gravado, sem escrever nada."""
    plan = ImportPlan(report=report, problems=list(report.rejected_rows))
    mapping = institution_brokers()
    brokers = {broker.name.casefold(): broker for broker in db.session.scalars(select(Broker))}
    unmapped: set[str] = set()
    for row in report.rows:
        broker_name = mapping.get(row.institution.casefold())
        broker = brokers.get(broker_name.casefold()) if broker_name else None
        if broker is None:
            unmapped.add(row.institution)
        else:
            plan.broker_ids[row.institution.casefold()] = broker.id
    for institution in sorted(unmapped):
        plan.problems.append(
            f"A instituição '{institution}' não corresponde a nenhuma corretora cadastrada."
        )

    documents = sorted({row.document for row in report.rows})
    existing: dict[tuple[str, int], Execution] = {}
    if documents:
        for record in db.session.scalars(
            select(Execution).where(Execution.owner_id == owner_id, Execution.document.in_(documents))
        ):
            existing[(record.document, record.line_order)] = record

    seen: set[tuple[str, int]] = set()
    for row in report.rows:
        key = (row.document, row.line_order)
        seen.add(key)
        record = existing.get(key)
        if record is None:
            plan.new_rows.append(row)
        elif record.content_hash == row.content_hash:
            plan.repeated += 1
        else:
            plan.changed.append((record, row))
    plan.absent_from_file = len(existing.keys() - seen)
    return plan


def apply_import(owner_id: int, plan: ImportPlan, *, file_name: str | None) -> ExecutionImport:
    """Grava o plano; ``commit`` é de quem chama.

    Recusa um plano com pendências: a rota e o comando só chegam aqui depois de
    mostrar o ensaio, mas a regra mora aqui para que nenhum caminho a pule.
    """
    if plan.problems:
        raise ValueError("O ensaio tem pendências; nada foi gravado.")
    report = plan.report
    batch = ExecutionImport(
        owner_id=owner_id,
        # Ponto único de gravação do nome: o que parece CPF sai daqui.
        file_name=safe_file_name(file_name),
        period_start=report.period_start,
        period_end=report.period_end,
        sha256=report.sha256,
        row_count=len(report.rows),
        rows_created=len(plan.new_rows),
        rows_updated=len(plan.changed),
    )
    db.session.add(batch)
    db.session.flush()
    for row in plan.new_rows:
        db.session.add(
            Execution(
                owner_id=owner_id,
                import_id=batch.id,
                broker_id=plan.broker_ids[row.institution.casefold()],
                document=row.document,
                line_order=row.line_order,
                **{name: getattr(row, name) for name in _EXECUTION_FIELDS},
            )
        )
    for record, row in plan.changed:
        record.import_id = batch.id
        record.broker_id = plan.broker_ids[row.institution.casefold()]
        for name in _EXECUTION_FIELDS:
            setattr(record, name, getattr(row, name))
    db.session.flush()
    return batch
