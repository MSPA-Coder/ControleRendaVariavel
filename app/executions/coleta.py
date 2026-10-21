"""A coleta do myProfit, do lado do CRV: negócios, posições e proventos.

O pacote ``myprofit-coleta/1`` sai de ``scripts/myprofit/coletar.js``, rodado na
aba do myProfit já logada, e é o mesmo que o Controle Bancário importa (lá ele
vira saldos de Tesouro, renda fixa EUA, caixa em dólar e remessas). O script só
copia campos de uma lista fixa: nome, CPF, e-mail e telefone, que as APIs do
myProfit também devolvem, nunca entram no pacote.

Aqui ele alimenta o que já existia pelos relatórios, pelo mesmo caminho:

* ``negocios_tsv`` é o "Histórico de investimentos" do período, já anonimizado,
  e entra por ``plan_import``/``apply_import`` (linha repetida é reconhecida);
* ``posicoes`` é a posição por corretora em cada data (fins de mês e o dia da
  coleta); cada data vira um retrato em ``myprofit_positions``, substituindo o da
  mesma data, como o relatório "Posição detalhada";
* ``proventos`` é conferido contra os proventos do CRV (que vêm do CB): por
  ativo e mês, nas corretoras do CB, a partir do corte do CB e até
  ``MARGEM_DE_PROVENTOS`` dias antes da coleta (o crédito recente ainda pode não
  ter chegado ao CB). O resultado fica em ``myprofit_coletas`` para o status de
  fechamento ler.

O módulo lê e confere; gravar é do comando, que delimita a transação.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select

from app import db
from app.executions.myprofit import (
    ASSET_GROUPS,
    B3_GROUPS,
    ExecutionReport,
    _fold,
    read_execution_report,
)
from app.executions.posicao_myprofit import PositionReport, PositionRow
from app.models import Broker, Dividend, IncomeKind, MyprofitColeta, Ticker

FORMATO = "myprofit-coleta/1"
LIMITE_DE_BYTES = 4 * 1024 * 1024
#: Proventos pagos nos últimos dias antes da coleta ficam de fora da conferência.
MARGEM_DE_PROVENTOS = 7
TOLERANCIA_DE_PROVENTOS = Decimal("1.00")
#: Corretoras cujo provento em reais vem do CB (``origin = 'cb'``).
CORRETORAS_DO_CB = ("Genial", "XP")


class ColetaError(ValueError):
    """Pacote que não pode ser importado. A mensagem nunca traz conteúdo do pacote."""


@dataclass(frozen=True, slots=True)
class Pacote:
    sha256: str
    de: date
    ate: date
    posicoes: dict[date, list[dict]]
    negocios_tsv: str
    proventos: list[dict]


@dataclass(slots=True)
class ConferenciaDeProventos:
    inicio: date | None
    fim: date | None
    conferem: int = 0
    divergencias: list[str] = field(default_factory=list)

    def resumo(self) -> dict:
        return {
            "inicio": self.inicio.isoformat() if self.inicio else None,
            "fim": self.fim.isoformat() if self.fim else None,
            "conferem": self.conferem,
            "divergencias": self.divergencias,
        }


def _data(valor, campo: str) -> date:
    try:
        return date.fromisoformat(str(valor)[:10])
    except (TypeError, ValueError) as exc:
        raise ColetaError(f"Data inválida em {campo}.") from exc


def _decimal(valor) -> Decimal:
    if valor in (None, ""):
        return Decimal(0)
    try:
        numero = Decimal(str(valor))
    except InvalidOperation as exc:
        raise ColetaError("Número inválido no pacote.") from exc
    if not numero.is_finite():
        raise ColetaError("Número inválido no pacote.")
    return numero


def _linhas_da_tabela(posicoes) -> list[dict]:
    """As posições de uma data como dicionários: o pacote as manda como tabela
    (``{"colunas": [...], "linhas": [[...], ...]}``) ou como lista de objetos."""
    if isinstance(posicoes, dict):
        colunas, linhas = posicoes.get("colunas"), posicoes.get("linhas")
        if not isinstance(colunas, list) or not isinstance(linhas, list):
            raise ColetaError("As posições do pacote estão num formato inválido.")
        return [dict(zip(colunas, linha, strict=False)) for linha in linhas if isinstance(linha, list)]
    if isinstance(posicoes, list):
        return [linha for linha in posicoes if isinstance(linha, dict)]
    raise ColetaError("As posições do pacote estão num formato inválido.")


def ler(conteudo: bytes) -> Pacote:
    if not conteudo:
        raise ColetaError("Envie o pacote .json da coleta.")
    if len(conteudo) > LIMITE_DE_BYTES:
        raise ColetaError("O pacote passa de 4 MB.")
    try:
        dados = json.loads(conteudo.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ColetaError("O arquivo não é um pacote JSON da coleta do myProfit.") from exc
    if not isinstance(dados, dict) or dados.get("formato") != FORMATO:
        raise ColetaError(f"O arquivo não é um pacote {FORMATO}.")
    de, ate = _data(dados.get("de"), "de"), _data(dados.get("ate"), "ate")
    if ate < de:
        raise ColetaError("O período do pacote é inválido.")
    posicoes: dict[date, list[dict]] = {}
    for chave, linhas in (dados.get("posicoes") or {}).items():
        dia = _data(chave, "posicoes")
        if dia > ate:
            raise ColetaError("O pacote traz posição depois da data da coleta.")
        posicoes[dia] = _linhas_da_tabela(linhas)
    negocios = dados.get("negocios_tsv") or ""
    if not isinstance(negocios, str):
        raise ColetaError("Os negócios do pacote estão num formato inválido.")
    proventos = [p for p in dados.get("proventos") or [] if isinstance(p, dict)]
    return Pacote(hashlib.sha256(conteudo).hexdigest(), de, ate, posicoes, negocios, proventos)


def retratos(pacote: Pacote) -> list[PositionReport]:
    """Cada data do pacote como um retrato da "Posição detalhada" (sem corretora)."""
    resultado = []
    for dia in sorted(pacote.posicoes):
        rows = []
        for ordem, linha in enumerate(pacote.posicoes[dia], start=1):
            grupo = " ".join(str(linha.get("grupo") or "").split())[:40]
            asset_group = ASSET_GROUPS.get(_fold(grupo))
            quantidade = _decimal(linha.get("quantidade"))
            preco = _decimal(linha.get("preco_medio"))
            if quantidade < 0 or preco < 0 or not str(linha.get("ticker") or "").strip() or not grupo:
                raise ColetaError(f"Posição inválida em {dia:%d/%m/%Y}, linha {ordem}.")
            rows.append(PositionRow(
                source_row=ordem,
                asset=" ".join(str(linha["ticker"]).split())[:80],
                group_label=grupo,
                asset_group=asset_group,
                quantity=quantidade,
                average_price=preco,
                total_invested=_decimal(linha.get("custo_brl")),
                in_scope=asset_group in B3_GROUPS,
            ))
        material = f"{pacote.sha256}:{dia.isoformat()}".encode()
        resultado.append(PositionReport(as_of=dia, sha256=hashlib.sha256(material).hexdigest(), rows=rows, rejected_rows=[]))
    return resultado


def negocios(pacote: Pacote) -> ExecutionReport | None:
    if not pacote.negocios_tsv.strip():
        return None
    return read_execution_report(pacote.negocios_tsv.encode("utf-8"))


def conferir_proventos(owner_id: int, pacote: Pacote, corte_do_cb: date | None) -> ConferenciaDeProventos:
    """Por ativo e mês de pagamento: o que o myProfit diz contra o que o CRV tem.

    O CRV guarda o líquido que entrou no caixa (o do CB) e, ao lado, o bruto
    (``gross_amount``); o myProfit dá o total e o imposto. Confere quando o total
    do myProfit bate com o bruto do CRV, ou o total menos o imposto bate com o
    líquido, dentro de ``TOLERANCIA_DE_PROVENTOS``.

    O aluguel de ações fica de fora: cada lado usa uma convenção (o CB chama de
    bruto o crédito antes da parte da corretora, cerca de duas vezes o líquido; o
    myProfit informa algo perto do líquido mais o IR), e nenhuma regra fixa leva
    um ao outro. Comparar acusaria diferença todo mês sem haver erro.

    A janela termina no último provento que o CB já trouxe ao CRV: o crédito de
    um extrato ainda não importado não é diferença, é extrato que falta.
    """
    inicio = max(pacote.de, corte_do_cb) if corte_do_cb else pacote.de
    fim = pacote.ate - timedelta(days=MARGEM_DE_PROVENTOS)
    ultimo_do_cb = db.session.scalar(
        select(func.max(Dividend.payment_date)).where(Dividend.owner_id == owner_id, Dividend.origin == "cb")
    )
    if ultimo_do_cb is not None:
        fim = min(fim, ultimo_do_cb)
    conferencia = ConferenciaDeProventos(inicio, fim)
    if fim < inicio:
        return conferencia
    corretoras = {nome.casefold() for nome in CORRETORAS_DO_CB}
    deles: dict[tuple[str, str], tuple[Decimal, Decimal]] = defaultdict(lambda: (Decimal(0), Decimal(0)))
    for provento in pacote.proventos:
        dia = _data(provento.get("data_pagamento"), "proventos")
        corretora = str(provento.get("corretora") or "").strip().casefold()
        if not inicio <= dia <= fim or corretora not in corretoras:
            continue
        if _fold(str(provento.get("tipo") or "")) == "aluguel":
            continue
        chave = (str(provento.get("ticker") or "").strip().upper(), f"{dia:%Y-%m}")
        total, imposto = deles[chave]
        deles[chave] = (total + _decimal(provento.get("valor")), imposto + _decimal(provento.get("imposto")))
    nossos: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    brutos: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for simbolo, dia, valor, bruto in db.session.execute(
        select(Ticker.symbol, Dividend.payment_date, Dividend.amount, Dividend.gross_amount)
        .join(Ticker, Ticker.id == Dividend.ticker_id)
        .join(Broker, Broker.id == Dividend.broker_id)
        .where(
            Dividend.owner_id == owner_id,
            Dividend.superseded_at.is_(None),
            Dividend.payment_date >= inicio,
            Dividend.payment_date <= fim,
            Dividend.kind != IncomeKind.ALUGUEL,
            Broker.name.in_(CORRETORAS_DO_CB),
        )
    ):
        nossos[(simbolo.upper(), f"{dia:%Y-%m}")] += valor
        brutos[(simbolo.upper(), f"{dia:%Y-%m}")] += bruto if bruto is not None else valor
    for chave in sorted(deles.keys() | nossos.keys()):
        total, imposto = deles.get(chave, (Decimal(0), Decimal(0)))
        nosso = nossos.get(chave, Decimal(0))
        bruto = brutos.get(chave, Decimal(0))
        if (
            abs(bruto - total) <= TOLERANCIA_DE_PROVENTOS
            or abs(nosso - total) <= TOLERANCIA_DE_PROVENTOS
            or abs(nosso - (total - imposto)) <= TOLERANCIA_DE_PROVENTOS
        ):
            conferencia.conferem += 1
            continue
        ticker, mes = chave
        conferencia.divergencias.append(
            f"{ticker} {mes[5:]}/{mes[:4]}: myProfit R$ {total:.2f}, CRV R$ {nosso:.2f}"
        )
    return conferencia


def registrar(owner_id: int, pacote: Pacote, conferencia: ConferenciaDeProventos) -> MyprofitColeta:
    """Guarda a coleta (ou atualiza a do mesmo pacote). Não faz commit."""
    existente = db.session.scalar(
        select(MyprofitColeta).where(MyprofitColeta.owner_id == owner_id, MyprofitColeta.sha256 == pacote.sha256)
    )
    resumo = {"proventos": conferencia.resumo(), "datas": [dia.isoformat() for dia in sorted(pacote.posicoes)]}
    if existente is None:
        existente = MyprofitColeta(owner_id=owner_id, sha256=pacote.sha256, ate=pacote.ate, summary=resumo)
        db.session.add(existente)
    else:
        existente.summary = resumo
    db.session.flush()
    return existente


def ultima_coleta(owner_id: int) -> MyprofitColeta | None:
    return db.session.scalar(
        select(MyprofitColeta)
        .where(MyprofitColeta.owner_id == owner_id)
        .order_by(MyprofitColeta.ate.desc(), MyprofitColeta.id.desc())
        .limit(1)
    )
