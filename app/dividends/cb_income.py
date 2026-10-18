"""Proventos do Controle Bancario: do lancamento de caixa ao provento do CRV.

Funcoes puras, sem banco e sem rede: recebem os lancamentos ja lidos do CB
(``CbIncomeEntry``) e devolvem o que o CRV deve gravar e o que precisa de
revisao. A gravacao e a leitura do cadastro ficam em ``app.dividends.cb_sync``.

REGRA DO VALOR (decisao do mantenedor, 18/10/2026): o provento e o LIQUIDO do
caixa. Para cada credito (``income_role = credito``):

* dividendo e JCP nao tem retencao lancada: liquido = credito;
* aluguel: liquido = credito
  - o IRRF do aluguel do MESMO ativo, conta e dia (exato);
  - a corretagem do aluguel, as taxas pequenas e os estornos do dia na mesma
    conta. Esses nao trazem o ativo (ou trazem o de uma linha estornada), entao
    formam um so valor por conta e dia, rateado entre os creditos de aluguel do
    dia na proporcao do valor do credito;
* ``gross_amount`` guarda o credito e ``withheld_amount`` tudo o que foi retido.

O rateio usa o metodo do maior resto: a soma das partes e exatamente o total do
dia, ao centavo, e o arredondamento e sempre explicito.

Nada aqui calcula imposto de renda: o IRRF e o que o CB ja lancou.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, Decimal

from app.patrimonio.cb_client import CbIncomeEntry

CENT = Decimal("0.01")
ZERO = Decimal("0")

ROLE_CREDIT = "credito"
ROLE_IRRF = "irrf_aluguel"
ROLE_BROKERAGE = "corretagem_aluguel"
ROLE_FEE = "taxa_aluguel"
ROLE_REVERSAL = "estorno"
KIND_RENT = "aluguel"
KINDS = frozenset({KIND_RENT, "jcp", "dividendo"})

#: Motivos da fila de revisao. O texto de cada um diz o que o mantenedor faz.
REASON_NO_INSTRUMENT = "sem_ativo"
REASON_UNKNOWN_TICKER = "ticker_desconhecido"
REASON_NO_KIND = "sem_tipo"
REASON_NOT_POSITIVE = "liquido_nao_positivo"
REASON_ORPHAN_WITHHOLDING = "retencao_sem_credito"
REASON_NEGATIVE_POOL = "estorno_maior_que_retencao"
REASON_UNHANDLED = "papel_nao_tratado"

_REASON_TEXT = {
    REASON_NO_INSTRUMENT: "O CB não informou o ativo deste crédito. Corrija o lançamento no CB (campo de ativo).",
    REASON_UNKNOWN_TICKER: "O ticker não existe no cadastro do CRV. Cadastre o ticker e rode o comando de novo; o CRV não cria ticker sozinho.",
    REASON_NO_KIND: "O CB não classificou o tipo de renda (aluguel, JCP ou dividendo) deste crédito.",
    REASON_NOT_POSITIVE: "O líquido do aluguel (crédito menos as retenções do dia) não é positivo; confira o rateio no CB.",
    REASON_ORPHAN_WITHHOLDING: "Retenção do CB sem crédito de aluguel para recebê-la (mesmo dia e conta).",
    REASON_NEGATIVE_POOL: "Os estornos do dia superam a corretagem e as taxas lançadas; confira no CB.",
    REASON_UNHANDLED: "Lançamento de renda com papel que o CRV não trata; confira no CB.",
}


@dataclass(frozen=True, slots=True)
class CbProvento:
    """Um provento do CB pronto para virar uma linha ``origin = 'cb'``."""

    origin_id: str
    broker: str
    institution: str
    ticker: str
    kind: str
    payment_date: date
    gross: Decimal
    withheld: Decimal

    @property
    def net(self) -> Decimal:
        return self.gross - self.withheld


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """Uma pendencia para o mantenedor; nada e gravado a partir dela."""

    reason: str
    entry_id: str
    payment_date: date
    institution: str
    instrument: str | None
    kind: str | None
    amount: Decimal
    description: str

    @property
    def message(self) -> str:
        return _REASON_TEXT[self.reason]


@dataclass(slots=True)
class IncomeResult:
    proventos: list[CbProvento] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)
    before_cutoff: int = 0
    other_institution: int = 0
    other_currency: int = 0

    @property
    def gross_total(self) -> Decimal:
        return sum((item.gross for item in self.proventos), ZERO)

    @property
    def withheld_total(self) -> Decimal:
        return sum((item.withheld for item in self.proventos), ZERO)

    @property
    def net_total(self) -> Decimal:
        return self.gross_total - self.withheld_total


def split_by_weight(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Reparte ``total`` (em centavos) na proporcao de ``weights``, sem perder centavo.

    Maior resto: cada parte nasce truncada no centavo e os centavos que sobram
    vao, um a um, para as maiores fracoes (empate: a primeira da lista). A soma
    das partes e sempre ``total``.
    """
    if not weights:
        return []
    weight_sum = sum(weights, ZERO)
    if weight_sum <= 0:
        raise ValueError("A soma dos pesos deve ser positiva.")
    exact = [total * weight / weight_sum for weight in weights]
    parts = [value.quantize(CENT, rounding=ROUND_DOWN) for value in exact]
    leftover = int(((total - sum(parts, ZERO)) / CENT).to_integral_value())
    order = sorted(range(len(parts)), key=lambda index: (-(exact[index] - parts[index]), index))
    for index in order[:leftover]:
        parts[index] += CENT
    return parts


def _review(reason: str, entry: CbIncomeEntry) -> ReviewItem:
    return ReviewItem(
        reason=reason,
        entry_id=entry.entry_id,
        payment_date=entry.realized_date,
        institution=entry.institution,
        instrument=entry.instrument,
        kind=entry.kind,
        amount=entry.amount,
        description=entry.description,
    )


def build_proventos(
    entries: Collection[CbIncomeEntry],
    *,
    cutoff: date,
    broker_by_institution: Mapping[str, str],
    known_tickers: Collection[str],
    currency: str = "BRL",
) -> IncomeResult:
    """Transforma os lancamentos de renda do CB nos proventos do CRV.

    So entram lancamentos em ``currency`` (reais), de instituicoes mapeadas a uma
    corretora do CRV e com data realizada ``>= cutoff``. ``known_tickers`` e o
    cadastro de tickers do CRV: credito de ativo desconhecido vai para a fila de
    revisao, e nunca cria ticker.
    """
    result = IncomeResult()
    scoped: list[CbIncomeEntry] = []
    for entry in entries:
        if entry.currency != currency:
            result.other_currency += 1
        elif entry.institution not in broker_by_institution:
            result.other_institution += 1
        elif entry.realized_date < cutoff:
            result.before_cutoff += 1
        else:
            scoped.append(entry)
    scoped.sort(key=lambda item: (item.realized_date, item.entry_id))

    credits = [entry for entry in scoped if entry.role == ROLE_CREDIT]
    rent_credits: dict[tuple[str, date], list[CbIncomeEntry]] = defaultdict(list)
    for entry in credits:
        if entry.kind == KIND_RENT:
            rent_credits[(entry.account_id, entry.realized_date)].append(entry)

    irrf_by_credit: dict[str, Decimal] = defaultdict(Decimal)
    pool_by_credit: dict[str, Decimal] = defaultdict(Decimal)

    # IRRF: exato, conta + ativo + dia.
    irrf_groups: dict[tuple[str, str | None, date], Decimal] = defaultdict(Decimal)
    irrf_entries: dict[tuple[str, str | None, date], list[CbIncomeEntry]] = defaultdict(list)
    # Corretagem, taxas e estornos: um valor por conta e dia.
    pool_groups: dict[tuple[str, date], Decimal] = defaultdict(Decimal)
    pool_entries: dict[tuple[str, date], list[CbIncomeEntry]] = defaultdict(list)
    for entry in scoped:
        if entry.role == ROLE_CREDIT:
            continue
        if entry.role == ROLE_IRRF:
            key3 = (entry.account_id, entry.instrument, entry.realized_date)
            irrf_groups[key3] += entry.amount
            irrf_entries[key3].append(entry)
        elif entry.role in {ROLE_BROKERAGE, ROLE_FEE}:
            key2 = (entry.account_id, entry.realized_date)
            pool_groups[key2] += entry.amount
            pool_entries[key2].append(entry)
        elif entry.role == ROLE_REVERSAL and entry.kind == KIND_RENT:
            key2 = (entry.account_id, entry.realized_date)
            pool_groups[key2] -= entry.amount
            pool_entries[key2].append(entry)
        else:
            result.review.append(_review(REASON_UNHANDLED, entry))

    for (account_id, instrument, day), total in irrf_groups.items():
        receivers = [
            credit for credit in rent_credits.get((account_id, day), []) if credit.instrument == instrument
        ]
        if not receivers:
            result.review.extend(_review(REASON_ORPHAN_WITHHOLDING, entry) for entry in irrf_entries[(account_id, instrument, day)])
            continue
        parts = split_by_weight(total, [credit.amount for credit in receivers])
        for credit, part in zip(receivers, parts, strict=True):
            irrf_by_credit[credit.entry_id] += part

    for (account_id, day), total in pool_groups.items():
        receivers = rent_credits.get((account_id, day), [])
        if total == 0:
            # Estorno e relancamento do mesmo valor no dia (acontece sem credito
            # de aluguel no dia): efeito zero no caixa, nada a atribuir.
            continue
        if not receivers:
            result.review.extend(_review(REASON_ORPHAN_WITHHOLDING, entry) for entry in pool_entries[(account_id, day)])
            continue
        if total < 0:
            result.review.extend(_review(REASON_NEGATIVE_POOL, entry) for entry in pool_entries[(account_id, day)])
            continue
        parts = split_by_weight(total, [credit.amount for credit in receivers])
        for credit, part in zip(receivers, parts, strict=True):
            pool_by_credit[credit.entry_id] += part

    for entry in credits:
        if entry.kind not in KINDS:
            result.review.append(_review(REASON_NO_KIND, entry))
            continue
        if not entry.instrument:
            result.review.append(_review(REASON_NO_INSTRUMENT, entry))
            continue
        if entry.instrument not in known_tickers:
            result.review.append(_review(REASON_UNKNOWN_TICKER, entry))
            continue
        withheld = irrf_by_credit.get(entry.entry_id, ZERO) + pool_by_credit.get(entry.entry_id, ZERO)
        if entry.amount - withheld <= 0:
            result.review.append(_review(REASON_NOT_POSITIVE, entry))
            continue
        result.proventos.append(
            CbProvento(
                origin_id=entry.entry_id,
                broker=broker_by_institution[entry.institution],
                institution=entry.institution,
                ticker=entry.instrument,
                kind=entry.kind,
                payment_date=entry.realized_date,
                gross=entry.amount,
                withheld=withheld,
            )
        )
    result.review.sort(key=lambda item: (item.payment_date, item.entry_id))
    return result
