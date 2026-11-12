"""Excluir a transação de um encerramento TOTAL desfaz o encerramento (ação e opção).

Com o arquivo completo do extrato a posição volta idêntica (ids, datas,
quantidade, custo médio, vínculo das baixas), as linhas dos dois arquivos saem e
o espelho aberto volta; se já há posição viva com a mesma chave, recusa; sem o
arquivo completo (encerramento antigo) a transação é apagada e a posição sai da
série de desempenho, só com a confirmação. Cada cenário roda nos dois lados e a
igualdade entre eles é parte da asserção (ver `test_paridade_acao_opcao.py`).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, func, select

from app import db
from app.executions.conferencia import ledger_trades
from app.models import (
    AuditLog,
    Broker,
    PatrimonioV4Outbox,
    PositionLedgerArchive,
    Side,
    Transaction,
    TransactionStatus,
)
from app.patrimonio.queries import position_timeline
from app.positions.reopen import classify
from tests.test_financial_isolation_http import client_for
from tests.test_financial_isolation_http import review_case as review_case
from tests.test_paridade_acao_opcao import (
    ABERTURA,
    LADOS,
    REFORCO,
    _dono,
    _encerrar,
    _observar,
    _registrar,
)
from tests.test_paridade_acao_opcao import (
    _limpar_arquivo_do_extrato as _limpar_arquivo_do_extrato,
)

pytestmark = pytest.mark.banco

PARCIAL = date(2026, 6, 30)
TOTAL = date(2026, 7, 10)


def _abrir_reforcar_baixar(lado, dados) -> int:
    pid = _registrar(lado, dados).position.id
    _registrar(lado, dados, opened_on=REFORCO, average_cost=Decimal("20"))
    _encerrar(lado, dados, pid, "25", PARCIAL, "40")
    return pid


def _estado(lado, dados, pid) -> dict[str, Any]:
    """O que se vê da posição, mais o que o usuário não vê mas a identifica."""
    visto = _observar(lado, dados, pid)
    posicao = db.session.get(lado.modelo, pid)
    visto["ids_do_extrato"] = [] if posicao is None else [(m.id, m.transaction_id) for m in posicao.movements]
    visto["criado_em_do_extrato"] = [] if posicao is None else [m.created_at for m in posicao.movements]
    return visto


def _linhas_de_arquivo(lado, dados, pid) -> tuple[int, int]:
    completo = db.session.scalar(
        select(func.count()).select_from(lado.modelo_arquivo).where(
            lado.modelo_arquivo.owner_id == _dono(dados), lado.modelo_arquivo.source_position_id == pid
        )
    )
    serie = db.session.scalar(
        select(func.count()).select_from(PositionLedgerArchive).where(
            PositionLedgerArchive.owner_id == _dono(dados),
            PositionLedgerArchive.instrument == lado.nome,
            PositionLedgerArchive.source_position_id == pid,
        )
    )
    return completo, serie


def _excluir(app, dados, transacao_id, **campos) -> tuple[int, str]:
    cliente, token = client_for(app, dados["ids"][0])
    resposta = cliente.post(f"/transactions/{transacao_id}/delete", data={"csrf_token": token, **campos})
    pagina = cliente.get(resposta.headers["Location"]).get_data(as_text=True)
    return resposta.status_code, pagina


def _entidade(lado) -> str:
    return "OptionPosition" if lado.nome == "option" else "Position"


def _ambos(app, dados, cenario) -> dict[str, Any]:
    resultado = {}
    for nome, lado in LADOS.items():
        with app.app_context():
            resultado[nome] = cenario(lado, dados)
    return resultado


def test_ciclo_abrir_reforcar_baixar_encerrar_e_desfazer_volta_identico(review_case):
    app, dados = review_case
    corretora = None

    def preparar(lado, dados):
        nonlocal corretora
        pid = _abrir_reforcar_baixar(lado, dados)
        corretora = db.session.get(Broker, dados["broker"]).name
        antes = _estado(lado, dados, pid)
        serie_antes = _serie(dados, lado.nome)
        negocios_antes = ledger_trades(_dono(dados), [corretora]) if lado.nome == "stock" else None
        tid = _encerrar(lado, dados, pid, "18", TOTAL).id
        return {"pid": pid, "tid": tid, "antes": antes, "serie_antes": serie_antes,
                "negocios_antes": negocios_antes, "arquivo_no_encerramento": _linhas_de_arquivo(lado, dados, pid),
                "total_reconhecido": classify([db.session.get(Transaction, tid)]).get(tid)}

    preparado = _ambos(app, dados, preparar)
    assert {n: p["arquivo_no_encerramento"] for n, p in preparado.items()} == {
        "stock": (4, 4), "option": (4, 4)}
    assert all(p["total_reconhecido"] is True for p in preparado.values())

    janelas: dict[str, tuple[int, int]] = {}
    resultados = {}
    for nome in LADOS:
        with app.app_context():
            antes_do_feed = db.session.scalar(select(func.max(PatrimonioV4Outbox.cursor))) or 0
        resultados[nome] = _excluir(app, dados, preparado[nome]["tid"])[0]
        with app.app_context():
            janelas[nome] = (antes_do_feed, db.session.scalar(select(func.max(PatrimonioV4Outbox.cursor))) or 0)
    assert resultados == {"stock": 302, "option": 302}

    def conferir(lado, dados):
        p = preparado[lado.nome]
        pid = p["pid"]
        depois = _estado(lado, dados, pid)
        transacao = db.session.get(Transaction, p["tid"])
        eventos = db.session.execute(
            select(PatrimonioV4Outbox.resource, PatrimonioV4Outbox.operation).where(
                PatrimonioV4Outbox.cursor > janelas[lado.nome][0],
                PatrimonioV4Outbox.cursor <= janelas[lado.nome][1],
                PatrimonioV4Outbox.resource == "position_ledger",
            )
        ).all()
        auditoria = db.session.scalars(
            select(AuditLog).where(AuditLog.action == "desfazer", AuditLog.entity_id == str(pid),
                                   AuditLog.entity == _entidade(lado))
            .order_by(AuditLog.id.desc())
        ).first()
        return {
            "igual_ao_de_antes": depois == p["antes"],
            "transacao_espelho": (transacao.status, transacao.closed_on, transacao.exit_price, transacao.result),
            "arquivos": _linhas_de_arquivo(lado, dados, pid),
            "serie_igual": _serie(dados, lado.nome) == p["serie_antes"],
            "negocios_iguais": (
                None if p["negocios_antes"] is None
                else ledger_trades(_dono(dados), [db.session.get(Broker, dados["broker"]).name]) == p["negocios_antes"]
            ),
            "eventos_do_feed": sorted(eventos),
            "auditoria": None if auditoria is None else (auditoria.entity, auditoria.details["reaberta"]),
            "depois": depois,
        }

    observado = _ambos(app, dados, conferir)

    for nome, lado in observado.items():
        assert lado["igual_ao_de_antes"], nome
        assert lado["transacao_espelho"] == (TransactionStatus.OPEN, None, None, None)
        assert lado["arquivos"] == (0, 0)
        assert lado["serie_igual"], nome
    assert observado["stock"]["negocios_iguais"] is True
    # Só o arquivo das ações tem gatilho do outbox: as quatro linhas saem como `delete`.
    assert observado["stock"]["eventos_do_feed"] == [("position_ledger", "delete")] * 4
    assert observado["option"]["eventos_do_feed"] == []
    assert observado["stock"]["auditoria"] == ("Position", True)
    assert observado["option"]["auditoria"] == ("OptionPosition", True)
    # Ids e carimbos de criação são de cada tabela; o resto é o mesmo nos dois lados.
    visivel = {n: {k: v for k, v in lado["depois"].items() if k not in ("ids_do_extrato", "criado_em_do_extrato")}
               for n, lado in observado.items()}
    assert visivel["stock"] == visivel["option"]
    depois = observado["stock"]["depois"]
    assert depois["posicao"] == (ABERTURA, Decimal("160"), Decimal("15"), "C")
    assert [e[0] for e in depois["extrato"]] == ["open", "increase", "decrease"]


def test_depois_de_reaberta_a_baixa_parcial_ainda_se_desfaz(review_case):
    app, dados = review_case

    def preparar(lado, dados):
        pid = _abrir_reforcar_baixar(lado, dados)
        parcial_id = db.session.scalar(
            select(Transaction.id).where(
                Transaction.owner_id == _dono(dados), Transaction.status == TransactionStatus.CLOSED,
                getattr(Transaction, lado.coluna_transacao).is_not(None),
            )
        )
        tid = _encerrar(lado, dados, pid, "18", TOTAL).id
        return {"pid": pid, "tid": tid, "parcial": parcial_id}

    ids = _ambos(app, dados, preparar)
    for nome in LADOS:
        assert _excluir(app, dados, ids[nome]["tid"])[0] == 302
        assert _excluir(app, dados, ids[nome]["parcial"])[0] == 302

    def conferir(lado, dados):
        posicao = db.session.get(lado.modelo, ids[lado.nome]["pid"])
        return (posicao.quantity, posicao.average_cost, [m.kind.value for m in posicao.movements])

    assert _ambos(app, dados, conferir) == {
        "stock": (Decimal("200"), Decimal("15"), ["open", "increase"]),
        "option": (Decimal("200"), Decimal("15"), ["open", "increase"]),
    }


def test_posicao_viva_com_a_mesma_chave_recusa_e_nada_muda(review_case):
    app, dados = review_case

    def preparar(lado, dados):
        pid = _abrir_reforcar_baixar(lado, dados)
        tid = _encerrar(lado, dados, pid, "18", TOTAL).id
        nova = _registrar(lado, dados).position.id  # reaberta depois do encerramento
        return {"pid": pid, "tid": tid, "nova": nova, "antes": _estado(lado, dados, nova),
                "arquivos": _linhas_de_arquivo(lado, dados, pid)}

    ids = _ambos(app, dados, preparar)
    respostas = {nome: _excluir(app, dados, ids[nome]["tid"]) for nome in LADOS}
    for nome, (status, pagina) in respostas.items():
        assert status == 302
        assert "mesma carteira, corretora e tipo" in pagina, nome

    def conferir(lado, dados):
        p = ids[lado.nome]
        transacao = db.session.get(Transaction, p["tid"])
        return {
            "transacao": (transacao.status, transacao.closed_on),
            "posicao_antiga_existe": db.session.get(lado.modelo, p["pid"]) is not None,
            "arquivos": _linhas_de_arquivo(lado, dados, p["pid"]),
            "viva_intacta": _estado(lado, dados, p["nova"]) == p["antes"],
        }

    for nome, lado in _ambos(app, dados, conferir).items():
        assert lado == {
            "transacao": (TransactionStatus.CLOSED, TOTAL),
            "posicao_antiga_existe": False,
            "arquivos": ids[nome]["arquivos"],
            "viva_intacta": True,
        }, nome


def test_sem_arquivo_completo_exige_confirmacao_e_so_tira_a_posicao_da_serie(review_case):
    app, dados = review_case

    def preparar(lado, dados):
        pid = _abrir_reforcar_baixar(lado, dados)
        tid = _encerrar(lado, dados, pid, "18", TOTAL).id
        # Outra posição encerrada, que não pode ser tocada.
        outra = _registrar(lado, dados, opened_on=date(2026, 5, 4), average_cost=Decimal("7"),
                           side=Side.SELL).position.id
        _encerrar(lado, dados, outra, "9", date(2026, 5, 20))
        # Encerramento antigo: só a projeção da série sobrou.
        db.session.execute(delete(lado.modelo_arquivo).where(
            lado.modelo_arquivo.owner_id == _dono(dados), lado.modelo_arquivo.source_position_id == pid))
        db.session.commit()
        return {"pid": pid, "tid": tid, "outra": outra, "serie_da_outra": _linhas_de_arquivo(lado, dados, outra)[1],
                "reconhecido": classify([db.session.get(Transaction, tid)]).get(tid)}

    ids = _ambos(app, dados, preparar)
    assert [p["reconhecido"] for p in ids.values()] == [False, False]

    sem_confirmar = {nome: _excluir(app, dados, ids[nome]["tid"]) for nome in LADOS}
    for nome, (status, pagina) in sem_confirmar.items():
        assert status == 302 and "foi guardado" in pagina, nome

    def depois_da_recusa(lado, dados):
        p = ids[lado.nome]
        return (db.session.get(Transaction, p["tid"]) is not None, _linhas_de_arquivo(lado, dados, p["pid"]))

    assert _ambos(app, dados, depois_da_recusa) == {"stock": (True, (0, 4)), "option": (True, (0, 4))}

    for nome in LADOS:
        assert _excluir(app, dados, ids[nome]["tid"], confirm_sem_extrato="1")[0] == 302

    def depois_de_confirmar(lado, dados):
        p = ids[lado.nome]
        auditoria = db.session.scalars(
            select(AuditLog).where(AuditLog.action == "desfazer", AuditLog.entity_id == str(p["pid"]),
                                   AuditLog.entity == _entidade(lado))
        ).first()
        return {
            "transacao_existe": db.session.get(Transaction, p["tid"]) is not None,
            "posicao_existe": db.session.get(lado.modelo, p["pid"]) is not None,
            "serie_da_posicao": _linhas_de_arquivo(lado, dados, p["pid"])[1],
            "serie_da_outra": _linhas_de_arquivo(lado, dados, p["outra"])[1],
            "auditoria": None if auditoria is None else auditoria.details["reaberta"],
        }

    for nome, lado in _ambos(app, dados, depois_de_confirmar).items():
        assert lado == {
            "transacao_existe": False, "posicao_existe": False, "serie_da_posicao": 0,
            "serie_da_outra": ids[nome]["serie_da_outra"], "auditoria": False,
        }, nome


def test_baixa_parcial_de_posicao_encerrada_e_transacao_avulsa_nao_sao_encerramento_total(review_case):
    app, dados = review_case

    def cenario(lado, dados):
        pid = _abrir_reforcar_baixar(lado, dados)
        parcial = db.session.scalar(
            select(Transaction).where(
                Transaction.owner_id == _dono(dados), Transaction.status == TransactionStatus.CLOSED,
                getattr(Transaction, lado.coluna_transacao).is_not(None),
            )
        )
        total = _encerrar(lado, dados, pid, "18", TOTAL)
        return {
            "parcial": classify([parcial]).get(parcial.id),
            "total": classify([total]).get(total.id),
        }

    for lado in _ambos(app, dados, cenario).values():
        assert lado == {"parcial": None, "total": True}


def _serie(dados, instrumento: str) -> list[tuple]:
    """A série de desempenho do instrumento: quantidade por data de cada posição."""
    return sorted(
        (e.occurred_on, e.ticker_id, e.resulting_signed_quantity, e.position_key)
        for e in position_timeline(_dono(dados))
        if e.position_key[0] == instrumento
    )
