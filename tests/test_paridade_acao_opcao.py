"""Paridade entre posições de ação e de opção (ARQ-L1).

`app/positions/closure.py` e `app/options/closure.py` são módulos gêmeos que já
divergiram numa regra de dinheiro (ARQ-1: só a ação exigia confirmação para um
lote anterior à abertura). Este arquivo roda os MESMOS cenários nos dois lados,
pela porta pública (casos de uso `register_*`/`close_*` e rotas HTTP), e compara
o que um usuário observa: posição, extrato, transações e arquivo de encerramento.

Como ler:

- Teste que compara `observado["stock"] == observado["option"]`: os dois lados
  se comportam igual, e quem mudar só um deles reprova aqui.
- Teste `test_divergencia_*`: os dois lados DIFEREM hoje. Cada lado afirma o seu
  comportamento atual, com o motivo. A divergência não é endossada: ela está
  registrada para ficar visível e para travar se alguém a mudar sem querer. Quem
  a resolver (num lado ou nos dois) troca a asserção por igualdade.

Diferenças legítimas de domínio, que NÃO são divergência e por isso não são
comparadas: o ativo da ação é `ticker_id` e o da opção é `contract_id` (strike,
vencimento, ativo-objeto); a ação tem `target_multiplier` e a opção `target_price`;
a opção recusa abrir posição em contrato vencido (`permitir_contrato_vencido`);
a opção libera acesso ao ticker do CONTRATO, não ao do ativo-objeto; a chave do
lock consultivo e o filtro da transação aberta espelhada (`ticker_id` ×
`option_contract_id`) são próprios de cada tabela; o rótulo `instrument` no
arquivo de série é "stock"/"option"; o texto das mensagens de tela cita "ativo"
ou "contrato". O resultado financeiro de um encerramento é o mesmo
`operation_result` nos dois (não há multiplicador de contrato no cálculo).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app import db
from app.models import (
    OptionPosition,
    OptionPositionMovement,
    Position,
    PositionLedgerArchive,
    PositionMovement,
    PositionMovementArchive,
    PositionMovementKind,
    Transaction,
)
from app.positions.use_cases import (
    ConfirmationRequiredError,
    close_option,
    close_stock,
    register_option,
    register_stock,
)
from tests.test_financial_isolation_http import client_for
from tests.test_financial_isolation_http import review_case as review_case
from tests.test_position_use_cases import _command

pytestmark = pytest.mark.banco


@pytest.fixture(autouse=True)
def _limpar_arquivo_do_extrato(review_case):
    """Acompanha o `review_case` e apaga, antes dele, o arquivo de extrato de ações.

    O encerramento total de uma ação grava `PositionMovementArchive`, que a
    limpeza do fixture comum não conhece e cuja FK impede apagar a carteira.
    """
    app, dados = review_case
    yield
    with app.app_context():
        db.session.rollback()
        db.session.execute(
            delete(PositionMovementArchive).where(PositionMovementArchive.owner_id.in_(dados["user_ids"]))
        )
        db.session.commit()

ABERTURA = date(2026, 6, 10)
ANTERIOR = date(2026, 6, 1)
REFORCO = date(2026, 6, 20)
INTERMEDIARIA = date(2026, 6, 15)


@dataclass(frozen=True)
class Lado:
    nome: str
    modelo: type
    modelo_movimento: type
    registrar: Callable[..., Any]
    encerrar: Callable[..., Any]
    base: str  # prefixo das rotas da posição
    coluna_transacao: str  # distingue as transações de cada instrumento

    def comando(self, dados, **mudar):
        return replace(_command(dados, self.nome), **mudar)

    def formulario(self, dados, **campos):
        base = {
            "broker_id": dados["broker"], "portfolio_id": dados["portfolio_ids"][0],
            "quantity": "100", "average_cost": "10", "side": "C",
            "opened_on": ABERTURA.isoformat(), "submit_token": uuid4().hex,
        }
        if self.nome == "stock":
            base.update(ticker_id=dados["ticker_ids"][0], target_multiplier="1.5")
        else:
            base.update(contract_id=dados["contract"], target_price="")
        return {**base, **campos}


LADOS = {
    "stock": Lado("stock", Position, PositionMovement, register_stock, close_stock,
                  "/positions", "ticker_id"),
    "option": Lado("option", OptionPosition, OptionPositionMovement, register_option,
                   close_option, "/options/positions", "option_contract_id"),
}


def _dono(dados) -> int:
    return dados["user_ids"][0]


def _registrar(lado: Lado, dados, **mudar):
    return lado.registrar(lado.comando(dados, **mudar), owner_id=_dono(dados), token=uuid4().hex)


def _encerrar(lado: Lado, dados, pid, preco, dia, quantidade=None):
    return lado.encerrar(
        pid, Decimal(preco), dia, None if quantidade is None else Decimal(quantidade),
        owner_id=_dono(dados),
    )


def _observar(lado: Lado, dados, pid: int | None) -> dict[str, Any]:
    """O que o usuário vê, sem ids: posição, extrato e transações do instrumento."""
    db.session.expire_all()
    dono = _dono(dados)
    posicao = db.session.get(lado.modelo, pid) if pid is not None else None
    coluna = getattr(Transaction, lado.coluna_transacao)
    transacoes = db.session.scalars(
        select(Transaction).where(Transaction.owner_id == dono, coluna.is_not(None)).order_by(Transaction.id)
    ).all()
    return {
        "posicao": None if posicao is None else (
            posicao.opened_on, posicao.quantity, posicao.average_cost, posicao.side.value
        ),
        "extrato": [] if posicao is None else [
            (m.kind.value, m.quantity_delta, m.price, m.occurred_on, m.resulting_quantity,
             m.resulting_average_cost, m.result)
            for m in sorted(posicao.movements, key=lambda m: (m.occurred_on, m.id))
        ],
        "transacoes": [
            (t.status.value, t.quantity, t.average_cost, t.exit_price, t.opened_on, t.closed_on, t.result)
            for t in transacoes
        ],
    }


def _tentar(operacao: Callable[[], Any]) -> tuple[str, Any]:
    try:
        operacao()
    except ConfirmationRequiredError as exc:
        return "pede confirmacao", exc.prior_opening_date
    except ValueError as exc:
        return "recusa", str(exc)
    return "aceita", None


def _ambos(app, dados, cenario: Callable[[Lado, Any], dict[str, Any]]) -> dict[str, dict[str, Any]]:
    with app.app_context():
        return {nome: cenario(lado, dados) for nome, lado in LADOS.items()}


# --- criar posição ---------------------------------------------------------


def test_criar_posicao_e_reenvio_com_o_mesmo_token_tem_o_mesmo_efeito(review_case):
    app, dados = review_case

    def cenario(lado, dados):
        token = uuid4().hex
        comando = lado.comando(dados)
        primeiro = lado.registrar(comando, owner_id=_dono(dados), token=token)
        pid = primeiro.position.id
        depois_do_primeiro = _observar(lado, dados, pid)
        repetido = lado.registrar(comando, owner_id=_dono(dados), token=token)
        return {
            "primeiro": (primeiro.merged, primeiro.replayed, primeiro.prior_opening_date),
            "estado": depois_do_primeiro,
            "repetido": (repetido.replayed, repetido.position),
            "estado_apos_repeticao": _observar(lado, dados, pid),
        }

    observado = _ambos(app, dados, cenario)

    assert observado["stock"] == observado["option"]
    stock = observado["stock"]
    assert stock["primeiro"] == (False, False, None)
    assert stock["repetido"] == (True, None)
    assert stock["estado"] == stock["estado_apos_repeticao"]
    assert stock["estado"]["posicao"] == (ABERTURA, Decimal("100"), Decimal("10"), "C")
    assert [e[0] for e in stock["estado"]["extrato"]] == ["open"]
    assert [t[0] for t in stock["estado"]["transacoes"]] == ["open"]


# --- lote anterior à abertura ----------------------------------------------


def test_lote_anterior_a_abertura_sem_confirmacao_pede_confirmacao_e_nada_muda(review_case):
    app, dados = review_case

    def cenario(lado, dados):
        pid = _registrar(lado, dados).position.id
        antes = _observar(lado, dados, pid)
        resposta = _tentar(lambda: _registrar(
            lado, dados, opened_on=ANTERIOR, quantity=Decimal("50"), average_cost=Decimal("20")
        ))
        return {"resposta": resposta, "antes": antes, "depois": _observar(lado, dados, pid)}

    observado = _ambos(app, dados, cenario)

    assert observado["stock"] == observado["option"]
    assert observado["stock"]["resposta"] == ("pede confirmacao", ABERTURA)
    assert observado["stock"]["antes"] == observado["stock"]["depois"]


def test_lote_anterior_a_abertura_confirmado_vira_a_abertura_e_reaplica_o_extrato(review_case):
    app, dados = review_case

    def cenario(lado, dados):
        pid = _registrar(lado, dados).position.id
        resultado = lado.registrar(
            lado.comando(dados, opened_on=ANTERIOR, quantity=Decimal("50"), average_cost=Decimal("20")),
            owner_id=_dono(dados), token=uuid4().hex, confirm_prior_opening=True,
        )
        return {"resultado": (resultado.merged, resultado.prior_opening_date),
                "estado": _observar(lado, dados, pid)}

    observado = _ambos(app, dados, cenario)

    assert observado["stock"] == observado["option"]
    estado = observado["stock"]["estado"]
    assert observado["stock"]["resultado"] == (True, ABERTURA)
    assert estado["posicao"][:3] == (ANTERIOR, Decimal("150"), Decimal("13.33333333"))
    assert [(e[0], e[4]) for e in estado["extrato"]] == [
        ("open", Decimal("50")), ("increase", Decimal("150")),
    ]


# --- lote duplicado ---------------------------------------------------------


def test_lote_duplicado_sem_confirmacao_pede_confirmacao_e_com_confirmacao_soma(review_case):
    app, dados = review_case

    def cenario(lado, dados):
        pid = _registrar(lado, dados).position.id
        antes = _observar(lado, dados, pid)
        sem = _tentar(lambda: _registrar(lado, dados))
        depois_sem = _observar(lado, dados, pid)
        com = lado.registrar(lado.comando(dados), owner_id=_dono(dados), token=uuid4().hex,
                             confirm_duplicate=True)
        return {"sem": sem, "antes": antes, "depois_sem": depois_sem,
                "com": (com.merged, com.prior_opening_date), "depois_com": _observar(lado, dados, pid)}

    observado = _ambos(app, dados, cenario)

    assert observado["stock"] == observado["option"]
    stock = observado["stock"]
    assert stock["sem"] == ("pede confirmacao", None)
    assert stock["antes"] == stock["depois_sem"]
    assert stock["com"] == (True, None)
    assert stock["depois_com"]["posicao"][:3] == (ABERTURA, Decimal("200"), Decimal("10"))
    assert [e[0] for e in stock["depois_com"]["extrato"]] == ["open", "increase"]


# --- encerramento parcial e total -------------------------------------------


def _ciclo_de_encerramento(lado: Lado, dados) -> dict[str, Any]:
    ultimo_do_arquivo = db.session.scalar(select(db.func.max(PositionMovementArchive.id))) or 0
    pid = _registrar(lado, dados).position.id
    _registrar(lado, dados, opened_on=REFORCO, average_cost=Decimal("20"))
    parcial = _encerrar(lado, dados, pid, "25", date(2026, 6, 30), "40")
    resultado_parcial = (parcial.result, parcial.average_cost, parcial.quantity)
    apos_parcial = _observar(lado, dados, pid)
    total = _encerrar(lado, dados, pid, "18", date(2026, 7, 10))
    resultado_total = (total.status.value, total.result, total.average_cost, total.quantity)
    db.session.expire_all()
    serie = db.session.scalars(
        select(PositionLedgerArchive).where(
            PositionLedgerArchive.owner_id == _dono(dados), PositionLedgerArchive.instrument == lado.nome
        )
    ).all()
    return {
        "parcial": resultado_parcial,
        "apos_parcial": apos_parcial,
        "total": resultado_total,
        "apos_total": _observar(lado, dados, pid),
        "posicao_apagada": db.session.get(lado.modelo, pid) is None,
        "serie_de_performance": sorted((s.occurred_on, s.resulting_signed_quantity) for s in serie),
        "arquivo_do_extrato": db.session.scalars(
            select(PositionMovementArchive.kind).where(
                PositionMovementArchive.owner_id == _dono(dados),
                PositionMovementArchive.id > ultimo_do_arquivo,
            ).order_by(PositionMovementArchive.id)
        ).all(),
        "encerrar_de_novo": _encerrar(lado, dados, pid, "18", date(2026, 7, 11)),
    }


def test_encerramento_parcial_e_total_realizam_o_mesmo_resultado_e_a_mesma_serie(review_case):
    app, dados = review_case

    observado = _ambos(app, dados, _ciclo_de_encerramento)

    for lado in observado.values():
        lado.pop("arquivo_do_extrato")  # diverge: ver test_divergencia_arquivo_do_extrato
    assert observado["stock"] == observado["option"]
    stock = observado["stock"]
    assert stock["parcial"] == (Decimal("400"), Decimal("15"), Decimal("40"))
    assert stock["total"] == ("closed", Decimal("480"), Decimal("15"), Decimal("160"))
    assert stock["posicao_apagada"] is True
    assert stock["encerrar_de_novo"] is None  # já encerrada: nada a fazer
    assert [t[0] for t in stock["apos_total"]["transacoes"]] == ["closed", "closed"]
    assert stock["serie_de_performance"][-1] == (date(2026, 7, 10), Decimal("0"))


def test_divergencia_arquivo_do_extrato_no_encerramento_total(review_case):
    """DIVERGE (ARQ-L1): só a ação preserva o extrato completo no encerramento total.

    `_close_entirely` de `positions/closure.py` chama `archive_closed_stock_movements`
    (copia cada movimento e acrescenta uma linha `close`) para integrações futuras;
    o de `options/closure.py` só alimenta a série de performance
    (`PositionLedgerArchive`, igual nos dois). Quem uniformizar troca esta asserção.
    """
    app, dados = review_case

    observado = _ambos(app, dados, _ciclo_de_encerramento)

    assert observado["stock"]["arquivo_do_extrato"] == ["open", "increase", "decrease", "close"]
    assert observado["option"]["arquivo_do_extrato"] == []


# --- retroatividade ---------------------------------------------------------


def test_aporte_entre_movimentos_reaplica_o_extrato_igual(review_case):
    app, dados = review_case

    def cenario(lado, dados):
        pid = _registrar(lado, dados).position.id
        _registrar(lado, dados, opened_on=REFORCO, average_cost=Decimal("20"))
        _registrar(lado, dados, opened_on=INTERMEDIARIA, average_cost=Decimal("30"))
        return _observar(lado, dados, pid)

    observado = _ambos(app, dados, cenario)

    assert observado["stock"] == observado["option"]
    assert [(e[0], e[3], e[4]) for e in observado["stock"]["extrato"]] == [
        ("open", ABERTURA, Decimal("100")),
        ("increase", INTERMEDIARIA, Decimal("200")),
        ("increase", REFORCO, Decimal("300")),
    ]
    assert observado["stock"]["posicao"][:3] == (ABERTURA, Decimal("300"), Decimal("20"))


def test_encerramento_retroativo_parcial_vale_e_total_e_recusado_igual(review_case):
    app, dados = review_case

    def cenario(lado, dados):
        pid = _registrar(lado, dados).position.id
        _registrar(lado, dados, opened_on=REFORCO, average_cost=Decimal("20"))
        total = _tentar(lambda: _encerrar(lado, dados, pid, "25", INTERMEDIARIA))
        apos_total_recusado = _observar(lado, dados, pid)
        parcial = _encerrar(lado, dados, pid, "25", INTERMEDIARIA, "40")
        sem_saldo = _tentar(lambda: _encerrar(lado, dados, pid, "25", INTERMEDIARIA, "100"))
        return {"total": total, "apos_total_recusado": apos_total_recusado,
                "parcial": parcial.result, "sem_saldo": sem_saldo,
                "estado": _observar(lado, dados, pid)}

    observado = _ambos(app, dados, cenario)

    assert observado["stock"] == observado["option"]
    stock = observado["stock"]
    assert stock["total"][0] == "recusa" and "não pode anteceder" in stock["total"][1]
    assert [e[0] for e in stock["apos_total_recusado"]["extrato"]] == ["open", "increase"]
    assert stock["parcial"] == Decimal("600")  # (25 - 10) * 40: a média na data era 10
    assert stock["sem_saldo"][0] == "recusa"
    assert stock["estado"]["posicao"][1] == Decimal("160")


def test_desfazer_encerramento_parcial_pelas_transacoes_restaura_a_posicao_igual(review_case):
    app, dados = review_case

    def cenario(lado, dados):
        pid = _registrar(lado, dados).position.id
        _registrar(lado, dados, opened_on=REFORCO, average_cost=Decimal("20"))
        parcial = _encerrar(lado, dados, pid, "25", date(2026, 6, 30), "40")
        return {"pid": pid, "tid": parcial.id}

    with app.app_context():
        ids = {nome: cenario(lado, dados) for nome, lado in LADOS.items()}
    cliente, token = client_for(app, dados["ids"][0])
    respostas = {
        nome: cliente.post(f"/transactions/{ids[nome]['tid']}/delete", data={"csrf_token": token}).status_code
        for nome in LADOS
    }
    with app.app_context():
        observado = {nome: _observar(lado, dados, ids[nome]["pid"]) for nome, lado in LADOS.items()}

    assert respostas == {"stock": 302, "option": 302}
    assert observado["stock"] == observado["option"]
    assert observado["stock"]["posicao"][:3] == (ABERTURA, Decimal("200"), Decimal("15"))
    assert [t[0] for t in observado["stock"]["transacoes"]] == ["open"]


def test_aporte_retroativo_sobre_extrato_sem_abertura_e_recusado_nos_dois_sem_gravar(review_case):
    """Os dois recusam e não gravam nada; só a mensagem difere (ver o próximo teste)."""
    app, dados = review_case

    def cenario(lado, dados):
        pid = _registrar(lado, dados).position.id
        _registrar(lado, dados, opened_on=REFORCO, average_cost=Decimal("20"))
        posicao = db.session.get(lado.modelo, pid)
        abertura = next(m for m in posicao.movements if m.kind is PositionMovementKind.OPEN)
        abertura.kind = PositionMovementKind.INCREASE  # extrato legado sem abertura
        db.session.commit()
        antes = _observar(lado, dados, pid)
        resposta = _tentar(lambda: _registrar(
            lado, dados, opened_on=INTERMEDIARIA, average_cost=Decimal("30")
        ))
        return {"resposta": resposta, "antes": antes, "depois": _observar(lado, dados, pid)}

    observado = _ambos(app, dados, cenario)

    for lado in observado.values():
        assert lado["resposta"][0] == "recusa"
        assert lado["antes"] == lado["depois"]
        assert "precisa começar por uma abertura" in lado["resposta"][1]


def test_divergencia_mensagem_do_aporte_retroativo_sem_abertura(review_case):
    """DIVERGE (ARQ-L1, só texto): a opção confere antes de gravar; a ação grava e
    deixa o `replay_movements` recusar, com outra frase. O efeito é o mesmo
    (ValueError, transação desfeita); a diferença trava quem mexer num lado só."""
    app, dados = review_case

    def cenario(lado, dados):
        pid = _registrar(lado, dados).position.id
        _registrar(lado, dados, opened_on=REFORCO, average_cost=Decimal("20"))
        posicao = db.session.get(lado.modelo, pid)
        next(m for m in posicao.movements if m.kind is PositionMovementKind.OPEN).kind = (
            PositionMovementKind.INCREASE
        )
        db.session.commit()
        return _tentar(lambda: _registrar(lado, dados, opened_on=INTERMEDIARIA))

    observado = _ambos(app, dados, cenario)

    assert observado["stock"] == ("recusa", "O extrato precisa começar por uma abertura.")
    assert observado["option"] == (
        "recusa", "O extrato precisa começar por uma abertura para registrar este aporte."
    )


def _desfazer_baixa(cliente, token, transacao_id) -> tuple[int, bool]:
    """Exclui a baixa em Transações: (status, a tela mostrou a recusa de posição sem extrato)."""
    resposta = cliente.post(f"/transactions/{transacao_id}/delete", data={"csrf_token": token})
    pagina = cliente.get(resposta.headers["Location"]).get_data(as_text=True)
    return resposta.status_code, "anterior ao extrato" in pagina  # trecho ASCII: `tojson` escapa acentos


def test_desfazer_baixa_com_extrato_sem_abertura_recusa_e_nada_grava_igual(review_case):
    """Extrato legado sem abertura e exclusão da baixa em Transações.

    A opção retornava em silêncio do `replay_movements`, apagava a transação e
    deixava a quantidade sem a devolução do encerrado (dinheiro errado, sem aviso).
    Agora, como a ação, o `replay_movements` recusa; a rota desfaz a transação do
    banco, avisa na tela (redirecionamento com aviso, não erro 500) e nada é
    gravado: a baixa continua e a quantidade não muda.
    """
    app, dados = review_case

    def cenario(lado, dados):
        pid = _registrar(lado, dados).position.id
        parcial = _encerrar(lado, dados, pid, "25", date(2026, 6, 30), "40")
        tid = parcial.id
        posicao = db.session.get(lado.modelo, pid)
        posicao.movements.remove(next(m for m in posicao.movements if m.kind is PositionMovementKind.OPEN))
        db.session.commit()
        return {"pid": pid, "tid": tid}

    with app.app_context():
        ids = {nome: cenario(lado, dados) for nome, lado in LADOS.items()}
    cliente, token = client_for(app, dados["ids"][0])
    resultado: dict[str, Any] = {}
    for nome in LADOS:
        resposta = _desfazer_baixa(cliente, token, ids[nome]["tid"])
        with app.app_context():
            db.session.expire_all()
            posicao = db.session.get(LADOS[nome].modelo, ids[nome]["pid"])
            baixa_existe = db.session.get(Transaction, ids[nome]["tid"]) is not None
            resultado[nome] = (resposta, posicao.quantity, baixa_existe)

    assert resultado["stock"] == resultado["option"] == ((302, True), Decimal("60"), True)


def test_divergencia_edicao_e_exclusao_de_movimento_do_extrato(review_case):
    """DIVERGE (ARQ-L1): só a ação tem rotas para corrigir ou excluir um lançamento.

    `/positions/<id>/movements/<id>` (editar) e `.../delete` recalculam o extrato
    em ordem cronológica (inclusive a promoção de aporte a abertura). Para a opção
    não existe a rota (404): um lançamento retroativo errado só se corrige
    excluindo e recriando a posição. A edição da data de abertura e o desfazer da
    baixa pelas Transações existem nos dois e estão cobertos acima.
    """
    app, dados = review_case

    def cenario(lado, dados):
        pid = _registrar(lado, dados).position.id
        _registrar(lado, dados, opened_on=REFORCO, average_cost=Decimal("20"))
        posicao = db.session.get(lado.modelo, pid)
        aumento = next(m for m in posicao.movements if m.kind is PositionMovementKind.INCREASE)
        return {"pid": pid, "mid": aumento.id}

    with app.app_context():
        ids = {nome: cenario(lado, dados) for nome, lado in LADOS.items()}
    cliente, token = client_for(app, dados["ids"][0])
    editar, excluir, estados = {}, {}, {}
    for nome, lado in LADOS.items():
        caminho = f"{lado.base}/{ids[nome]['pid']}/movements/{ids[nome]['mid']}"
        editar[nome] = cliente.post(caminho, data={
            "csrf_token": token, "quantity": "100", "price": "30", "occurred_on": REFORCO.isoformat(),
        }).status_code
        with app.app_context():
            estados[nome] = _observar(lado, dados, ids[nome]["pid"])["posicao"]
        excluir[nome] = cliente.post(f"{caminho}/delete", data={"csrf_token": token}).status_code

    assert editar == {"stock": 302, "option": 404}
    assert excluir == {"stock": 302, "option": 404}
    assert estados["stock"][:3] == (ABERTURA, Decimal("200"), Decimal("20"))  # preço 20 -> 30 recalculado
    assert estados["option"][:3] == (ABERTURA, Decimal("200"), Decimal("15"))  # nada mudou


# --- data futura ------------------------------------------------------------


def test_criar_posicao_com_data_futura_e_recusado_igual(review_case):
    # A mensagem sai num atributo JSON (`tojson`, acentos em \uXXXX): confere-se o trecho ASCII.
    app, dados = review_case
    cliente, token = client_for(app, dados["ids"][0])
    futuro = (date.today() + timedelta(days=1)).isoformat()
    respostas = {}
    for nome, lado in LADOS.items():
        resposta = cliente.post(lado.base, data={
            **lado.formulario(dados, opened_on=futuro), "csrf_token": token,
        })
        respostas[nome] = (resposta.status_code, "pode estar no futuro" in resposta.get_data(as_text=True))
    with app.app_context():
        assert db.session.scalars(
            select(Position).where(Position.owner_id == _dono(dados))
        ).first() is None
        assert db.session.scalars(
            select(OptionPosition).where(OptionPosition.owner_id == _dono(dados))
        ).first() is None

    assert respostas == {"stock": (422, True), "option": (422, True)}


def test_encerrar_com_data_futura_e_recusado_igual_e_nada_muda(review_case):
    app, dados = review_case
    with app.app_context():
        ids = {nome: _registrar(lado, dados).position.id for nome, lado in LADOS.items()}
    cliente, token = client_for(app, dados["ids"][0])
    futuro = (date.today() + timedelta(days=1)).isoformat()
    resultado = {}
    for nome, lado in LADOS.items():
        resposta = cliente.post(f"{lado.base}/{ids[nome]}/close", data={
            "csrf_token": token, "exit_price": "25", "closed_on": futuro,
        }, follow_redirects=True)
        with app.app_context():
            estado = _observar(lado, dados, ids[nome])
        resultado[nome] = (
            resposta.status_code, "pode estar no futuro" in resposta.get_data(as_text=True),
            estado["posicao"][:3], [t[0] for t in estado["transacoes"]],
        )

    igual = (200, True, (ABERTURA, Decimal("100"), Decimal("10")), ["open"])
    assert resultado == {"stock": igual, "option": igual}


# --- confirmação pela tela --------------------------------------------------


def test_tela_pede_confirmacao_para_lote_anterior_e_para_duplicado_igual(review_case):
    app, dados = review_case
    cliente, token = client_for(app, dados["ids"][0])
    resultado = {}
    for nome, lado in LADOS.items():
        def enviar(lado=lado, **campos):
            return cliente.post(lado.base, data={**lado.formulario(dados, **campos), "csrf_token": token})
        primeiro = enviar().status_code
        anterior = enviar(opened_on=ANTERIOR, quantity="50", average_cost="20")
        duplicado = enviar()
        anterior_confirmado = enviar(
            opened_on=ANTERIOR, quantity="50", average_cost="20", confirm_prior_opening="1"
        ).status_code
        resultado[nome] = (
            primeiro, anterior.status_code,
            "Data anterior à abertura registrada." in anterior.get_data(as_text=True),
            duplicado.status_code, anterior_confirmado,
        )

    assert resultado["stock"] == resultado["option"] == (302, 409, True, 409, 302)


# --- posição legada sem nenhuma linha de extrato ------------------------------
#
# Em produção há opções abertas anteriores ao extrato (nenhuma linha em
# `option_position_movements`). Estes casos fixam o que cada fluxo normal faz com
# elas, nos dois instrumentos: encerrar (parcial e total), aportar hoje e editar
# funcionam e gravam números certos; desfazer a baixa parcial é recusado.


def _posicao_sem_extrato(lado: Lado, dados) -> int:
    from dataclasses import asdict

    from app.options import closure as opcoes
    from app.positions import closure as acoes

    posicao = lado.modelo(owner_id=_dono(dados), **asdict(lado.comando(dados)))
    db.session.add(posicao)
    db.session.flush()
    (acoes if lado.nome == "stock" else opcoes).create_open_transaction_for_position(posicao)
    db.session.commit()
    assert not posicao.movements
    return posicao.id


def _com_posicao_sem_extrato(app, dados, fluxo):
    with app.app_context():
        return {nome: fluxo(lado, _posicao_sem_extrato(lado, dados)) for nome, lado in LADOS.items()}


def test_sem_extrato_baixa_parcial_de_hoje_funciona_com_resultado_e_custo_certos(review_case):
    app, dados = review_case

    def fluxo(lado, pid):
        baixa = _encerrar(lado, dados, pid, "25", date.today(), "40")
        return {"baixa": (baixa.result, baixa.average_cost, baixa.quantity), "estado": _observar(lado, dados, pid)}

    observado = _com_posicao_sem_extrato(app, dados, fluxo)

    assert observado["stock"] == observado["option"]
    assert observado["stock"]["baixa"] == (Decimal("600"), Decimal("10"), Decimal("40"))
    assert observado["stock"]["estado"]["posicao"][:3] == (ABERTURA, Decimal("60"), Decimal("10"))
    assert [t[0] for t in observado["stock"]["estado"]["transacoes"]] == ["open", "closed"]


def test_sem_extrato_encerramento_total_funciona_com_resultado_certo(review_case):
    app, dados = review_case

    def fluxo(lado, pid):
        total = _encerrar(lado, dados, pid, "25", date.today())
        return {"total": (total.status.value, total.result, total.average_cost, total.quantity),
                "estado": _observar(lado, dados, pid)}

    observado = _com_posicao_sem_extrato(app, dados, fluxo)

    assert observado["stock"] == observado["option"]
    assert observado["stock"]["total"] == ("closed", Decimal("1500"), Decimal("10"), Decimal("100"))
    assert observado["stock"]["estado"]["posicao"] is None
    assert [t[0] for t in observado["stock"]["estado"]["transacoes"]] == ["closed"]


def test_sem_extrato_aporte_de_hoje_soma_com_custo_medio_ponderado(review_case):
    app, dados = review_case

    def fluxo(lado, pid):
        resultado = lado.registrar(
            lado.comando(dados, opened_on=date.today(), quantity=Decimal("50"), average_cost=Decimal("20")),
            owner_id=_dono(dados), token=uuid4().hex,
        )
        return {"merged": resultado.merged, "estado": _observar(lado, dados, pid)}

    observado = _com_posicao_sem_extrato(app, dados, fluxo)

    assert observado["stock"] == observado["option"]
    assert observado["stock"]["merged"] is True
    assert observado["stock"]["estado"]["posicao"][:3] == (ABERTURA, Decimal("150"), Decimal("13.33333333"))
    assert [e[0] for e in observado["stock"]["estado"]["extrato"]] == ["increase"]


def test_sem_extrato_desfazer_a_baixa_parcial_e_recusado_e_nada_grava(review_case):
    """Depois de a baixa parcial (agora com linha de extrato `decrease`, sem abertura),
    excluí-la em Transações é recusado. Antes de `ca4692a` a opção apagava a
    transação e deixava a quantidade em 60 (devia voltar a 100): 40 de quantidade
    e R$ 600 de resultado sumiam da posição sem aviso. A ação sempre recusou."""
    app, dados = review_case

    def fluxo(lado, pid):
        baixa = _encerrar(lado, dados, pid, "25", date.today(), "40")
        return {"pid": pid, "tid": baixa.id}

    with app.app_context():
        ids = {nome: fluxo(lado, _posicao_sem_extrato(lado, dados)) for nome, lado in LADOS.items()}
    cliente, token = client_for(app, dados["ids"][0])
    resultado = {}
    for nome, lado in LADOS.items():
        resposta = _desfazer_baixa(cliente, token, ids[nome]["tid"])
        with app.app_context():
            db.session.expire_all()
            posicao = db.session.get(lado.modelo, ids[nome]["pid"])
            resultado[nome] = (resposta, posicao.quantity, db.session.get(Transaction, ids[nome]["tid"]) is not None)

    assert resultado["stock"] == resultado["option"] == ((302, True), Decimal("60"), True)


def test_sem_extrato_editar_pela_tela_funciona_e_grava_a_abertura(review_case):
    app, dados = review_case
    with app.app_context():
        ids = {nome: _posicao_sem_extrato(lado, dados) for nome, lado in LADOS.items()}
    cliente, token = client_for(app, dados["ids"][0])
    resultado = {}
    for nome, lado in LADOS.items():
        with app.app_context():
            versao = db.session.get(lado.modelo, ids[nome]).edit_version
        resposta = cliente.post(f"{lado.base}/{ids[nome]}", data={
            **lado.formulario(dados, average_cost="11"), "edit_version": str(versao), "csrf_token": token,
        })
        with app.app_context():
            estado = _observar(lado, dados, ids[nome])
        resultado[nome] = (resposta.status_code, estado["posicao"][:3], [e[0] for e in estado["extrato"]],
                           [t[1:3] for t in estado["transacoes"]])

    assert resultado["stock"] == resultado["option"]
    assert resultado["stock"][:3] == (302, (ABERTURA, Decimal("100"), Decimal("11")), ["open"])
