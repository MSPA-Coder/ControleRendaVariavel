"""Importar a coleta do myProfit: o caso de uso do comando e da tela.

Os dois caminhos passam por aqui para não haver duas regras: :func:`planejar`
lê o pacote e diz o que seria gravado e o que impede a gravação, sem tocar em
nada; :func:`gravar` grava o plano inteiro. Nenhum dos dois faz commit: a
transação é de quem chama.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from flask import current_app

from app.executions import coleta
from app.executions.conciliacao import save_position_snapshot
from app.executions.myprofit import ExecutionReportError
from app.executions.posicao_myprofit import PositionReport
from app.executions.service import ImportPlan, InstitutionMapError, apply_import, plan_import


class ColetaRecusadaError(ValueError):
    """O pacote não pode ser lido ou a configuração não permite conferi-lo."""


@dataclass(slots=True)
class PlanoDaColeta:
    pacote: coleta.Pacote
    retratos: list[PositionReport]
    #: ``None`` quando o pacote não traz o histórico de investimentos.
    negocios: ImportPlan | None
    proventos: coleta.ConferenciaDeProventos
    #: O que impede a gravação. Com algum impedimento, nada é gravado.
    impedimentos: list[str] = field(default_factory=list)

    @property
    def pode_gravar(self) -> bool:
        return not self.impedimentos


def corte_do_cb() -> date | None:
    bruto = str(current_app.config.get("PROVENTOS_CB_CORTE") or "").strip()
    try:
        return date.fromisoformat(bruto) if bruto else None
    except ValueError as exc:
        raise ColetaRecusadaError("PROVENTOS_CB_CORTE deve usar AAAA-MM-DD.") from exc


def planejar(owner_id: int, conteudo: bytes, *, hoje: date | None = None) -> PlanoDaColeta:
    """O ensaio: lê o pacote e confere, sem gravar."""
    if len(conteudo) > coleta.LIMITE_DE_BYTES:
        raise ColetaRecusadaError("O pacote passa de 4 MB.")
    try:
        pacote = coleta.ler(conteudo)
        retratos = coleta.retratos(pacote)
        relatorio = coleta.negocios(pacote)
        negocios = plan_import(owner_id, relatorio) if relatorio is not None else None
    except (coleta.ColetaError, ExecutionReportError, InstitutionMapError) as exc:
        raise ColetaRecusadaError(str(exc)) from exc
    plano = PlanoDaColeta(
        pacote=pacote,
        retratos=retratos,
        negocios=negocios,
        proventos=coleta.conferir_proventos(owner_id, pacote, corte_do_cb()),
    )
    if negocios is not None and negocios.problems:
        plano.impedimentos.append("Os negócios têm pendências; nada foi gravado.")
    if any(retrato.as_of > (hoje or date.today()) for retrato in retratos):
        plano.impedimentos.append("O pacote traz posição com data futura; refaça a coleta.")
    return plano


def gravar(owner_id: int, plano: PlanoDaColeta, *, file_name: str | None) -> None:
    """Grava negócios, retratos de posição e a conferência de proventos. Não faz commit."""
    if not plano.pode_gravar:
        raise ColetaRecusadaError(plano.impedimentos[0])
    if plano.negocios is not None and plano.negocios.can_write:
        apply_import(owner_id, plano.negocios, file_name=file_name)
    for retrato in plano.retratos:
        save_position_snapshot(owner_id, retrato)
    coleta.registrar(owner_id, plano.pacote, plano.proventos)
