"""Conversão datada para os totais da carteira.

O CRV só negocia BRL e USD. A cotação de referência ``USDBRL=X`` registra
quantos reais valem um dólar em cada fechamento. A conversão segue as mesmas
quatro regras do consolidado: usa a data solicitada, nunca busca uma taxa
posterior, aceita a última anterior em fins de semana/feriados e recusa uma
taxa com mais de sete dias. Um total parcial seria pior do que manter os blocos
por moeda separados.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

MOEDA_BASE = "BRL"
DIAS_MAXIMOS_DE_DEFASAGEM = 7
CENTAVO = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class TaxaDeCambio:
    taxa: Decimal
    data: date
    fonte: str = "USDBRL=X"

    def defasada_em(self, referencia: date) -> bool:
        return (referencia - self.data).days > DIAS_MAXIMOS_DE_DEFASAGEM


@dataclass(frozen=True, slots=True)
class ConversaoDaCarteira:
    total: Decimal | None
    taxa: TaxaDeCambio | None
    motivo: str = ""

    @property
    def possivel(self) -> bool:
        return self.total is not None


def converter_totais(
    totais: Iterable[tuple[str, Decimal]], *, referencia: date, taxa_usd_brl: TaxaDeCambio | None
) -> ConversaoDaCarteira:
    """Converte todos os blocos para BRL, ou não converte nenhum deles."""

    blocos = list(totais)
    if not blocos:
        return ConversaoDaCarteira(None, None, "não há posições cotadas para converter")
    moedas = {moeda for moeda, _ in blocos}
    if moedas - {"BRL", "USD"}:
        return ConversaoDaCarteira(None, None, "há moeda sem série de câmbio configurada")
    if "USD" in moedas:
        if taxa_usd_brl is None:
            return ConversaoDaCarteira(None, None, "sem taxa USD/BRL até a data da tela")
        if taxa_usd_brl.data > referencia:
            return ConversaoDaCarteira(None, taxa_usd_brl, "a taxa disponível é posterior à data da tela")
        if taxa_usd_brl.defasada_em(referencia):
            return ConversaoDaCarteira(
                None,
                taxa_usd_brl,
                f"taxa USD/BRL de {taxa_usd_brl.data.strftime('%d/%m/%Y')} está velha demais",
            )
    total = sum(
        (
            valor if moeda == MOEDA_BASE else valor * taxa_usd_brl.taxa  # type: ignore[union-attr]
            for moeda, valor in blocos
        ),
        Decimal("0"),
    )
    return ConversaoDaCarteira(
        total.quantize(CENTAVO, rounding=ROUND_HALF_UP), taxa_usd_brl if "USD" in moedas else None
    )
