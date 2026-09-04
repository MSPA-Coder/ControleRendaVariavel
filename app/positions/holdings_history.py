"""Histórico de posição e fluxo, para o TWR (Time-Weighted Return) da carteira.

O relatório precisa da quantidade vigente em cada data e do fluxo dos
aportes; usar a quantidade atual em toda a série cria patrimônio inexistente,
enquanto ignorar o fluxo transforma aporte em retorno. Por isso este módulo
produz as duas informações juntas: quantidade histórica
(``QuantityTimeline``) e fluxo líquido por data
(``PortfolioFlowPoint.net_flow``).

O modelo, em quatro peças:

- ``HoldingEvent`` é uma linha do extrato (``position_movements`` ou
  ``option_position_movements``) já traduzida para o sinal do lado:
  quantidade resultante positiva para posição comprada, negativa para
  vendida. A tradução em si (ler o extrato, aplicar o sinal) mora na camada
  de leitura, não aqui. O evento NÃO carrega o preço lançado — ver
  ``portfolio_flow_series`` para por que o fluxo é avaliado a preço de
  mercado da data e não ao preço do extrato.
- ``QuantityTimeline`` responde "quanto da carteira eu tinha, daquele
  ticker, naquela data", somando a última quantidade conhecida de cada
  posição do ticker até a data pedida.
- ``portfolio_flow_series`` junta quantidade histórica e cotação para
  produzir o patrimônio dia a dia, com o aporte líquido (``net_flow``) e a
  renda recebida (``income_by_kind``) de cada ponto separados do valor — é essa
  separação que permite ao índice TWR (``twr_index_series``) neutralizar o
  fluxo em vez de contá-lo como desempenho.
- ``prorate_dividends`` existe porque ``Dividend`` não conhece carteira nem
  corretora, só ticker: a renda é rateada pela fração da posição que o
  recorte (carteira ou corretora filtrada) detinha na data do pagamento.

Módulo de domínio puro: só ``Decimal`` e biblioteca padrão. Nada de
SQLAlchemy, Flask ou ``app.models`` — quem monta ``HoldingEvent`` a partir do
banco é a camada de leitura (``app.routes.helpers``).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class HoldingEvent:
    """Uma linha do extrato de movimentação (ação ou opção), já traduzida
    para o sinal do lado (``side``): ``+1`` compra, ``-1`` venda."""

    occurred_on: date
    ticker_id: int
    resulting_signed_quantity: Decimal
    """``sinal_side × resulting_quantity``: a quantidade da posição
    IMEDIATAMENTE APÓS este evento, já com o sinal do lado."""
    position_key: tuple[str, int]
    """``("stock", id)`` ou ``("option", id)``. Identifica a POSIÇÃO, não o
    ticker: ``Position`` e ``OptionPosition`` têm sequências de ``id``
    independentes, então uma ação e uma opção podem compartilhar o mesmo
    inteiro e o mesmo ticker."""


@dataclass(frozen=True, slots=True)
class DividendEvent:
    """Uma renda recebida pelo ticker — dividendo, JCP ou aluguel de ações
    (``kind``, ver ``app.models.IncomeKind``). Também é o tipo de retorno de
    ``prorate_dividends``: mesma forma, valor já rateado pelo recorte.

    As três entram no retorno do mesmo jeito; ``kind`` existe para o
    relatório poder dizer QUANTO cada uma rendeu, porque resultado por preço
    médio e preço de saída sozinho mascara a renda."""

    payment_date: date
    ticker_id: int
    amount: Decimal
    kind: str = "dividendo"


@dataclass(frozen=True, slots=True)
class PortfolioFlowPoint:
    """Um ponto da carteira na malha de datas: o patrimônio (``value``) e o
    que o moveu sem ser desempenho (``net_flow``, ``income_by_kind``) nessa
    data — a separação que ``twr_index_series`` usa para neutralizar
    fluxo."""

    observed_date: date
    value: Decimal
    net_flow: Decimal
    income_by_kind: Mapping[str, Decimal]
    """Renda creditada nesta data, separada por ``IncomeKind``. Só os tipos
    com valor aparecem. O TWR usa a soma (``income``); a separação existe
    para o relatório mostrar quanto veio de cada renda."""

    @property
    def income(self) -> Decimal:
        """Renda total da data — o que entra no numerador do retorno."""
        return sum(self.income_by_kind.values(), Decimal("0"))


class QuantityTimeline:
    """Quantidade histórica por ticker, reconstruída do extrato de
    movimentações.

    Para cada posição (``position_key``), guarda a série
    ``(occurred_on, resulting_signed_quantity)`` ordenada por data e
    consultada por ``bisect`` — a régua do módulo é "quantas posições ×
    quantos pontos no tempo", potencialmente centenas de consultas por
    relatório, e não compensa varrer o extrato inteiro a cada uma.
    """

    def __init__(self, events: Sequence[HoldingEvent]) -> None:
        events_by_position: dict[tuple[str, int], list[HoldingEvent]] = {}
        ticker_by_position: dict[tuple[str, int], int] = {}
        for event in events:
            events_by_position.setdefault(event.position_key, []).append(event)
            # Assume-se uma posição para um único ticker (invariante da
            # origem dos dados); um evento posterior com ticker divergente
            # apenas sobrescreveria, sem detecção — não há o que fazer aqui
            # sem tocar banco, e a leitura já garante essa consistência.
            ticker_by_position[event.position_key] = event.ticker_id

        # Por posição: datas e quantidades em listas paralelas, ordenadas
        # por data. Empate no mesmo dia (duas linhas de extrato na mesma
        # data) resolve pela ordem de entrada em `events` — `sorted` é
        # estável, então o último elemento do empate é o último que o
        # chamador listou para aquele dia.
        self._series_by_position: dict[tuple[str, int], tuple[list[date], list[Decimal]]] = {}
        positions_by_ticker: dict[int, list[tuple[str, int]]] = {}
        for position_key, position_events in events_by_position.items():
            ordered = sorted(position_events, key=lambda item: item.occurred_on)
            self._series_by_position[position_key] = (
                [item.occurred_on for item in ordered],
                [item.resulting_signed_quantity for item in ordered],
            )
            ticker_id = ticker_by_position[position_key]
            positions_by_ticker.setdefault(ticker_id, []).append(position_key)

        self._positions_by_ticker = positions_by_ticker
        self._ticker_ids = sorted(positions_by_ticker)

    def quantity_at(self, ticker_id: int, on: date) -> Decimal:
        """Soma, sobre as posições do ticker, a última quantidade
        conhecida até ``on`` (inclusive). Posição sem nenhum evento até
        essa data ainda não existia — contribui zero, não erro."""
        total = Decimal("0")
        for position_key in self._positions_by_ticker.get(ticker_id, []):
            dates, quantities = self._series_by_position[position_key]
            index = bisect_right(dates, on) - 1
            if index >= 0:
                total += quantities[index]
        return total

    @property
    def ticker_ids(self) -> list[int]:
        """Tickers com pelo menos um evento, ordenados."""
        return list(self._ticker_ids)


def prorate_dividends(
    dividends: Sequence[DividendEvent],
    filtered: QuantityTimeline,
    total: QuantityTimeline,
) -> list[DividendEvent]:
    """Rateia cada provento pela fração da posição que o recorte filtrado
    detinha na data do pagamento — necessário porque ``Dividend`` não
    conhece carteira nem corretora, só o ticker (ver docstring do módulo).

    ``abs()`` nas duas quantidades porque uma posição vendida tem
    quantidade negativa, e o rateio é sobre TAMANHO da posição, não sobre o
    sinal — sem o ``abs()``, um recorte vendido dentro de um total comprado
    (ou vice-versa) produziria uma razão negativa sem sentido econômico.

    Descarta (não gera ``DividendEvent`` nenhum, nem com valor zero) quando
    o total é zero — posição encerrada na data do pagamento, extrato
    apagado em cascata, nada a ratear — ou quando o recorte é zero — o
    filtro não detinha nada daquele ticker naquela data. A razão é
    truncada em 1 como cinto de segurança: as duas quantidades vêm de
    linhas do tempo construídas do mesmo extrato (uma filtrada, uma não) e
    por definição a filtrada não deveria superar a total, mas a truncagem
    evita um crédito acima de 100% caso as duas divirjam por qualquer
    motivo. A ordem de entrada de ``dividends`` é preservada.
    """
    result: list[DividendEvent] = []
    for dividend in dividends:
        filtered_quantity = abs(filtered.quantity_at(dividend.ticker_id, dividend.payment_date))
        total_quantity = abs(total.quantity_at(dividend.ticker_id, dividend.payment_date))
        if total_quantity == 0 or filtered_quantity == 0:
            continue
        ratio = filtered_quantity / total_quantity
        if ratio > 1:
            ratio = Decimal("1")
        result.append(
            DividendEvent(
                payment_date=dividend.payment_date,
                ticker_id=dividend.ticker_id,
                amount=dividend.amount * ratio,
                kind=dividend.kind,
            )
        )
    return result


def _bucket_totals(
    items: Iterable[tuple[date, Decimal]], all_dates: Sequence[date]
) -> dict[date, Decimal]:
    """Soma valores datados na malha de datas do relatório, agrupando cada
    item no primeiro ponto da malha ``>= `` sua data.

    Existe porque ``occurred_on``/``payment_date`` podem cair num dia sem
    cotação (fim de semana, feriado): sem esse reagrupamento, o aporte de
    um sábado desapareceria da malha em vez de aparecer no pregão seguinte.
    Item datado depois do último ponto da malha fica de fora — não há para
    onde adiantar.
    """
    totals: dict[date, Decimal] = {}
    for occurred_on, amount in items:
        index = bisect_left(all_dates, occurred_on)
        if index >= len(all_dates):
            continue
        bucket = all_dates[index]
        totals[bucket] = totals.get(bucket, Decimal("0")) + amount
    return totals


def portfolio_flow_series(
    events: Sequence[HoldingEvent],
    price_series: Mapping[int, Sequence[tuple[date, Decimal]]],
    dividends: Sequence[DividendEvent] = (),
) -> list[PortfolioFlowPoint]:
    """Patrimônio dia a dia, com aporte líquido e proventos de cada data
    separados do valor — a matéria-prima do índice TWR
    (``twr_index_series``).

    A malha de datas é a união das cotações dos tickers com pelo menos um
    evento (não de todo ``price_series``: um ticker sem posição não deveria
    determinar em que dias a carteira é observada). Cada preço é forward
    fill do último valor conhecido, com um cursor por ticker que avança
    monotonicamente ao longo da malha, para não repetir a busca do preço do
    zero a cada data.

    **O fluxo é avaliado a PREÇO DE MERCADO da data, não ao preço lançado
    no extrato** — ``Δquantidade × cotação``, e não ``Δquantidade × preço
    pago``. A diferença não é teórica: medido contra o preço pago, o fluxo
    só cancela o salto do patrimônio se as duas grandezas coincidirem, e
    nos dados reais elas divergem por três motivos que não têm nada a ver
    com desempenho — ``opened_on`` de uma posição antiga costuma ser a data
    em que ela foi CADASTRADA e não em que foi comprada; ``quote_history``
    guarda o fechamento AJUSTADO (proventos e splits) importado do Yahoo,
    enquanto o custo médio é nominal; e um ``ADJUSTMENT`` grava o custo
    médio no lugar de um preço de negócio. Qualquer uma dessas divergências
    vazaria para o retorno do dia do aporte como ganho ou perda que nunca
    aconteceu.

    Avaliando a mercado, o que entra na carteira vale exatamente o que a
    carteira ganha de valor, e o retorno do dia de um aporte é zero por
    construção. O preço é: perde-se o resultado real entre o preço de
    compra e o fechamento do MESMO dia. É um efeito pequeno, e a alternativa
    é deixar erro de cadastro contaminar a série inteira.

    Decorre daí que o fluxo é derivado da variação de quantidade
    (``QuantityTimeline``), e não somado dos eventos: é a variação que
    precisa ser casada com o preço da data. O dia em que um ticker ganha a
    PRIMEIRA cotação conhecida conta como entrada do seu valor inteiro —
    sem isso, ele apareceria do nada no patrimônio e o salto viraria
    retorno.
    """
    timeline = QuantityTimeline(events)
    tickers = timeline.ticker_ids
    if not tickers:
        return []

    sorted_series = {ticker_id: sorted(price_series.get(ticker_id, ())) for ticker_id in tickers}
    all_dates = sorted(
        {observed_date for series in sorted_series.values() for observed_date, _ in series}
    )
    if not all_dates:
        return []

    income_by_date: dict[date, dict[str, Decimal]] = {}
    for dividend in dividends:
        bucketed = _bucket_totals(((dividend.payment_date, dividend.amount),), all_dates)
        for bucket, amount in bucketed.items():
            by_kind = income_by_date.setdefault(bucket, {})
            by_kind[dividend.kind] = by_kind.get(dividend.kind, Decimal("0")) + amount

    cursors = dict.fromkeys(tickers, 0)
    last_known: dict[int, Decimal] = {}
    previous_quantity: dict[int, Decimal] = {}
    points: list[PortfolioFlowPoint] = []
    for current_date in all_dates:
        for ticker_id in tickers:
            series = sorted_series[ticker_id]
            cursor = cursors[ticker_id]
            while cursor < len(series) and series[cursor][0] <= current_date:
                last_known[ticker_id] = series[cursor][1]
                cursor += 1
            cursors[ticker_id] = cursor

        value = Decimal("0")
        net_flow = Decimal("0")
        for ticker_id, price in last_known.items():
            quantity = timeline.quantity_at(ticker_id, current_date)
            value += quantity * price
            if ticker_id in previous_quantity:
                net_flow += (quantity - previous_quantity[ticker_id]) * price
            else:
                # Primeira cotação conhecida deste ticker: todo o valor que
                # ele traz é entrada, não valorização.
                net_flow += quantity * price
            previous_quantity[ticker_id] = quantity

        points.append(
            PortfolioFlowPoint(
                observed_date=current_date,
                value=value,
                net_flow=net_flow,
                income_by_kind=income_by_date.get(current_date, {}),
            )
        )
    return points


def twr_index_series(points: Sequence[PortfolioFlowPoint]) -> list[tuple[date, Decimal]]:
    """Índice TWR (Time-Weighted Return) encadeado, base ``Decimal("1")``
    na primeira data de ``points``.

    Para cada ponto seguinte, o retorno do período é::

        r = (valor - valor_anterior - aporte_liquido + renda) / |valor_anterior|

    O aporte líquido é subtraído e a renda somada ANTES de dividir —
    é isso que neutraliza o fluxo: um aporte faz ``value`` saltar tanto
    quanto ``net_flow``, e a subtração cancela exatamente esse salto sem
    tocar o numerador quando não há aporte algum. O ``abs()`` no
    denominador é deliberado, não cosmético: com posição vendida o valor é
    negativo, e sem o ``abs()`` o sinal do retorno inverteria (uma perda
    pareceria ganho). Com ele, o denominador é sempre o capital empregado
    (magnitude), e o numerador carrega o resultado econômico com o sinal
    certo — comprado e vendido saem corretos pela mesma conta.

    Quando ``|valor_anterior| == 0`` o retorno não tem base para ser
    calculado (não existe "variação percentual" de uma posição zerada); em
    vez de propagar ``ZeroDivisionError``, o índice repete o valor
    anterior — invariante do projeto: divisão por zero produz estado
    definido.
    """
    if not points:
        return []
    # `portfolio_flow_series` já entrega em ordem de malha; ordenar de novo
    # é barato e evita depender silenciosamente dessa garantia do chamador.
    ordered = sorted(points, key=lambda point: point.observed_date)

    index = Decimal("1")
    result: list[tuple[date, Decimal]] = [(ordered[0].observed_date, index)]
    previous_value = ordered[0].value
    for point in ordered[1:]:
        base = abs(previous_value)
        if base != 0:
            r = (point.value - previous_value - point.net_flow + point.income) / base
            index = index * (1 + r)
        # `base == 0`: sem estado definido para o retorno, repete o índice
        # anterior em vez de levantar ZeroDivisionError.
        result.append((point.observed_date, index))
        previous_value = point.value
    return result
