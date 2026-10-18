"""YOC e DY de um provento que veio sem eles, calculados sobre as posicoes.

A planilha traz YOC, DY e cotas em cada linha; o provento do Controle Bancario
nao tem nenhum dos tres. Em vez de deixa-los em branco, a tela os calcula COM A
MESMA DEFINICAO que a importacao da planilha usa (``app.dividends.importer``):

* cotas = quantidade COMPRADA que a corretora detinha do ativo na data do
  pagamento (o mesmo criterio de ``QuantityTimeline.long_quantity_at``, que
  ignora quantidade vendida);
* YOC = recebido / (cotas x custo medio) x 100;
* DY  = recebido / (cotas x cotacao na data do pagamento) x 100.

Os valores sao pontos percentuais (0,42 = 0,42%), como nas colunas gravadas, e
nunca sao gravados: acompanham a posicao de hoje. Sem posicao na data (cotas
zero), o custo medio ou a cotacao, o campo correspondente fica vazio.

Funcao pura: quem chama entrega as consultas ja resolvidas (posicoes, custo
medio e serie de cotacoes), e o modulo nao toca o banco.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.domain import safe_div
from app.models import Dividend

HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class DerivedYield:
    quotas: Decimal
    yield_on_cost: Decimal | None
    dividend_yield: Decimal | None


def needs_derivation(dividend: Dividend) -> bool:
    """Linha sem cotas informadas (as do CB): e a unica que se calcula."""
    return dividend.quotas is None


def derive_yields(
    dividends: Iterable[Dividend],
    *,
    quantity_at: Callable[[int, int, date], Decimal],
    average_cost: Mapping[int, Decimal],
    price_on: Callable[[int, date], Decimal | None],
) -> dict[int, DerivedYield]:
    """YOC e DY por ``Dividend.id`` para as linhas sem cotas.

    ``quantity_at(corretora_id, ticker_id, data)`` devolve a quantidade comprada;
    ``price_on(ticker_id, data)`` devolve a cotacao do ultimo fechamento valido
    ate a data, ou ``None``.
    """
    result: dict[int, DerivedYield] = {}
    for dividend in dividends:
        if not needs_derivation(dividend):
            continue
        quotas = quantity_at(dividend.broker_id, dividend.ticker_id, dividend.payment_date)
        if quotas <= 0:
            continue
        cost = average_cost.get(dividend.ticker_id)
        price = price_on(dividend.ticker_id, dividend.payment_date)
        yield_on_cost = safe_div(dividend.amount * HUNDRED, quotas * cost) if cost else None
        dividend_yield = safe_div(dividend.amount * HUNDRED, quotas * price) if price else None
        result[dividend.id] = DerivedYield(quotas, yield_on_cost, dividend_yield)
    return result
