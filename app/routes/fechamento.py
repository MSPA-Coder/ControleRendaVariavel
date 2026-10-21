"""Status de fechamento: o que o CRV diz de si para o Roteiro de fechamento do CB.

``GET /fechamento/status?ate=AAAA-MM-DD`` (contrato ``crv-fechamento/1``). O
painel fica no Controle Bancário, que tem a maior parte das fontes; o que só o
CRV sabe -- coleta importada, negócios aplicados, posição e exterior conferidos
com o myProfit, pregões conferidos com o CB, proventos -- chega como itens já
avaliados, com etapa (``myprofit`` ou ``conferencias``), estado, texto e o que
fazer. O CB só os mostra.

Máquina a máquina, pela rede interna do VPS: sem sessão, autenticado pelo token
do vínculo CB <-> CRV, que aqui é o ``CB_PATRIMONIO_TOKEN`` (o mesmo segredo com
que o CRV lê o CB). Sem ele configurado, 503; com token errado, 401 -- o mesmo
contrato da publicação de patrimônio. Só leitura.

A conferência por pregão lê o CB de volta; para o painel não esperar por isso a
cada abertura, o resultado fica guardado por ``CACHE_DOS_PREGOES`` no processo.
"""

from __future__ import annotations

import hmac
import time
from datetime import date, timedelta

from flask import abort, current_app, jsonify, request
from sqlalchemy import func, select

from app import db
from app.executions import coleta as coleta_myprofit
from app.executions.aplicacao import pending_executions
from app.executions.conciliacao import STATE_LABELS as ESTADOS_DA_CONCILIACAO
from app.executions.conciliacao import build_conciliation, build_exterior
from app.executions.conferencia import (
    AWAITING_STATEMENT,
    DIFFERENCE,
    MATCH,
    ONLY_CB,
    ONLY_CRV,
    build_reconciliation,
)
from app.models import Execution, MyprofitPosition
from app.routes import bp
from app.routes.patrimonio import _owner_id

CONTRATO = "crv-fechamento/1"
#: Dias de atraso da integração da Avenue no myProfit: a posição do exterior é
#: conferida numa data pelo menos esta distância antes da coleta.
ATRASO_DA_AVENUE = 5
CACHE_DOS_PREGOES = 600

_pregoes_guardados: dict[tuple[int, date], tuple[float, dict]] = {}


def _exigir_token() -> None:
    configurado = str(current_app.config.get("CB_PATRIMONIO_TOKEN") or "")
    if not configurado:
        abort(503, "Status de fechamento não configurado.")
    if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {configurado}"):
        abort(401, "Não autorizado.")


def _dia(d: date) -> str:
    return f"{d:%d/%m}"


def _item(chave, etapa, titulo, estado, texto, *, detalhe="", acao="", url="", passos=None, subtitulo="CRV") -> dict:
    return {
        "chave": chave, "etapa": etapa, "titulo": titulo, "subtitulo": subtitulo, "estado": estado,
        "texto": texto, "detalhe": detalhe, "acao": acao, "url": url, "passos": passos or [],
    }


def _item_da_coleta(coleta, ate: date) -> dict:
    passos = [
        "Envie o pacote ao contêiner do CRV no VPS: "
        "ssh ubuntu@VPS1 'docker exec -i controle-renda-variavel-web-1 sh -c \"cat > /tmp/coleta.json\"' < coleta.json",
        "Ensaio: flask --app app:create_app importar-coleta-myprofit /tmp/coleta.json",
        "Gravar: o mesmo comando com --gravar. Reimportar é seguro: negócio repetido é reconhecido.",
    ]
    if coleta is None or coleta.ate < ate:
        detalhe = f"Última no CRV até {coleta.ate:%d/%m/%Y}." if coleta else "Nenhuma coleta importada no CRV."
        return _item("coleta", "myprofit", "Coleta no CRV", "falta", f"Importar a coleta até {_dia(ate)}",
                     detalhe=detalhe, acao="Importar no CRV", passos=passos)
    return _item("coleta", "myprofit", "Coleta no CRV", "em_dia", f"Importada até {_dia(coleta.ate)}")


def _item_dos_negocios(owner_id: int, ate: date) -> dict:
    pendentes = pending_executions(owner_id, until=ate)
    if pendentes:
        n = len(pendentes)
        return _item(
            "negocios", "myprofit", "Negócios da B3", "falta",
            f"{n} negócio{'s' if n > 1 else ''} para aplicar às posições",
            detalhe=f"O mais antigo é de {pendentes[0].trade_date:%d/%m/%Y}.",
            acao="Aplicar no CRV",
            passos=[
                "Ensaio: flask --app app:create_app aplicar-execucoes (mostra o que abre, reforça ou fecha).",
                "Gravar: o mesmo comando com --gravar. O que ele não resolver sozinho fica listado como pendência.",
            ],
        )
    ultima = db.session.scalar(
        select(func.max(Execution.trade_date)).where(
            Execution.owner_id == owner_id, Execution.in_scope.is_(True), Execution.trade_date <= ate
        )
    )
    texto = f"Aplicados até {_dia(ultima)}" if ultima else "Nenhum negócio importado"
    return _item("negocios", "myprofit", "Negócios da B3", "em_dia", texto)


def _item_da_posicao(owner_id: int, ate: date) -> dict:
    resultado = build_conciliation(owner_id, start=date(ate.year, 1, 1), end=ate)
    if resultado.as_of is None:
        return _item("posicao", "conferencias", "Posição da B3 × myProfit", "espera", "Sem posição do myProfit importada")
    pendentes = resultado.pending
    if pendentes:
        n = len(pendentes)
        detalhe = "; ".join(f"{f.asset}: {ESTADOS_DA_CONCILIACAO[f.state]}" for f in pendentes[:6])
        return _item(
            "posicao", "conferencias", "Posição da B3 × myProfit", "pendente",
            f"{n} diferença{'s' if n > 1 else ''} em {_dia(resultado.as_of)}", detalhe=detalhe,
            acao="Conciliar no CRV",
            passos=[
                "flask --app app:create_app conciliar-myprofit mostra cada diferença.",
                "Corrija o lançamento no CRV ou explique com reconhecer-conciliacao, para ela parar de contar.",
            ],
        )
    return _item("posicao", "conferencias", "Posição da B3 × myProfit", "em_dia", f"Confere em {_dia(resultado.as_of)}")


def _item_do_exterior(owner_id: int, coleta) -> dict:
    titulo = "Exterior × myProfit"
    if coleta is None:
        return _item("exterior", "conferencias", titulo, "espera", "Sem coleta do myProfit no CRV")
    limite = coleta.ate - timedelta(days=ATRASO_DA_AVENUE)
    data = db.session.scalar(
        select(func.max(MyprofitPosition.as_of)).where(
            MyprofitPosition.owner_id == owner_id, MyprofitPosition.as_of <= limite
        )
    )
    if data is None:
        return _item("exterior", "conferencias", titulo, "espera",
                     f"Sem posição do myProfit até {_dia(limite)}",
                     detalhe=f"A Avenue chega ao myProfit com até {ATRASO_DA_AVENUE} dias de atraso.")
    pendentes = [f for f in build_exterior(owner_id, data) if f.pending]
    if pendentes:
        detalhe = "; ".join(
            f"{f.asset}: myProfit {f.myprofit.quantity.normalize() if f.myprofit else 0}, "
            f"CRV {f.crv.quantity.normalize() if f.crv else 0}"
            for f in pendentes[:6]
        )
        n = len(pendentes)
        return _item(
            "exterior", "conferencias", titulo, "pendente", f"{n} diferença{'s' if n > 1 else ''} em {_dia(data)}",
            detalhe=detalhe,
            passos=["Confira no extrato da corretora. O que nunca vai bater (a corretora que o myProfit não lê) "
                    "fica reconhecido com reconhecer-conciliacao posicao ATIVO \"nota\"."],
        )
    return _item("exterior", "conferencias", titulo, "em_dia", f"Confere em {_dia(data)}",
                 detalhe=f"Data com {ATRASO_DA_AVENUE} dias de margem para o atraso da Avenue.")


def _resumo_dos_pregoes(owner_id: int, ate: date) -> dict:
    agora = time.monotonic()
    guardado = _pregoes_guardados.get((owner_id, ate))
    if guardado and agora - guardado[0] < CACHE_DOS_PREGOES:
        return guardado[1]
    resultado = build_reconciliation(owner_id, start=date(ate.year, 1, 1), end=ate)
    resumo = {"status": resultado.cb_status, "mensagem": resultado.cb_message, "contagem": resultado.counts}
    _pregoes_guardados[(owner_id, ate)] = (agora, resumo)
    return resumo


def _item_dos_pregoes(owner_id: int, ate: date) -> dict:
    titulo = "Pregões × liquidação no CB"
    resumo = _resumo_dos_pregoes(owner_id, ate)
    if resumo["status"] != "ok":
        return _item("pregoes", "conferencias", titulo, "espera", resumo["mensagem"] or "CB indisponível")
    contagem = resumo["contagem"]
    problemas = contagem.get(DIFFERENCE, 0) + contagem.get(ONLY_CB, 0) + contagem.get(ONLY_CRV, 0)
    if problemas:
        return _item(
            "pregoes", "conferencias", titulo, "pendente",
            f"{problemas} pregão{'ões' if problemas > 1 else ''} não confere{'m' if problemas > 1 else ''}",
            detalhe=f"Diferença {contagem.get(DIFFERENCE, 0)}, só no CB {contagem.get(ONLY_CB, 0)}, "
                    f"só no CRV {contagem.get(ONLY_CRV, 0)}.",
            acao="Abrir a conferência", url="/executions/reconciliation",
        )
    aguardando = contagem.get(AWAITING_STATEMENT, 0)
    if aguardando:
        return _item("pregoes", "conferencias", titulo, "espera",
                     f"{aguardando} pregão{'ões' if aguardando > 1 else ''} aguardando o extrato no CB",
                     url="/executions/reconciliation")
    return _item("pregoes", "conferencias", titulo, "em_dia", f"{contagem.get(MATCH, 0)} conferem",
                 url="/executions/reconciliation")


def _item_dos_proventos(coleta) -> dict:
    titulo = "Proventos × myProfit"
    if coleta is None:
        return _item("proventos", "conferencias", titulo, "espera", "Sem coleta do myProfit no CRV")
    resumo = (coleta.summary or {}).get("proventos") or {}
    divergencias = resumo.get("divergencias") or []
    periodo = ""
    if resumo.get("inicio") and resumo.get("fim"):
        periodo = f"De {date.fromisoformat(resumo['inicio']):%d/%m} a {date.fromisoformat(resumo['fim']):%d/%m}."
    if divergencias:
        n = len(divergencias)
        return _item("proventos", "conferencias", titulo, "pendente",
                     f"{n} ativo{'s' if n > 1 else ''} com diferença no mês",
                     detalhe=" ".join([periodo, "; ".join(divergencias[:6])]).strip(),
                     passos=["Os proventos do CRV vêm do CB: confira o crédito no extrato e a classificação no CB."])
    return _item("proventos", "conferencias", titulo, "em_dia", f"{resumo.get('conferem', 0)} conferem",
                 detalhe=periodo)


@bp.get("/fechamento/status")
def fechamento_status():
    _exigir_token()
    try:
        ate = date.fromisoformat(request.args.get("ate", ""))
    except ValueError:
        abort(400, "ate deve usar AAAA-MM-DD")
    owner_id = _owner_id()
    coleta = coleta_myprofit.ultima_coleta(owner_id)
    itens = [
        _item_da_coleta(coleta, ate),
        _item_dos_negocios(owner_id, ate),
        _item_da_posicao(owner_id, ate),
        _item_do_exterior(owner_id, coleta),
        _item_dos_pregoes(owner_id, ate),
        _item_dos_proventos(coleta),
    ]
    resposta = jsonify({"contrato": CONTRATO, "ate": ate.isoformat(), "itens": itens})
    resposta.headers["Cache-Control"] = "no-store"
    return resposta
