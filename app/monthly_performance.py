"""Relatório de Performance mensal: quantidade histórica e retorno TWR.

Consome ``app.holdings_history`` — domínio puro que traduz o extrato
(``position_movements``/``option_position_movements``, já reunidos pela
rota) em ``HoldingEvent`` (quantidade e fluxo assinados por data) e
``DividendEvent`` (provento já rateado pelo recorte de carteira/corretora
escolhido). Este módulo não acessa ORM nem faz I/O — só stdlib e
``app.holdings_history`` — e decide apenas COMO reduzir a série diária que
``portfolio_flow_series``/``twr_index_series`` produzem a um ponto por mês.

Substitui a aproximação antiga — quantidades ATUAIS das posições REAIS
tratadas como constantes ao longo de todo o histórico simulado, que o
docstring anterior descrevia como "sem tratamento rigoroso de
aportes/retiradas (sem Dietz/IRR)" — pelo modelo descrito em
``docs/planilha-acoes.md`` ("Performance mensal"): quantidade histórica
reconstruída do próprio extrato (a última ``resulting_quantity`` conhecida
até cada data, nunca a de hoje aplicada ao passado) e retorno TWR
encadeado, em que cada elo neutraliza o aporte/retirada do dia no
numerador — um aumento de posição não pode aparecer como desempenho — e
credita o provento recebido nesse mesmo numerador, porque este app não tem
conta caixa: sem esse crédito, o dinheiro do provento simplesmente some na
data ex (o preço cai, o patrimônio cai, e nada compensa).

O comparador de índice (``build_benchmark_shadow_series``) acumula COTAS
hipotéticas do benchmark a partir dos MESMOS fluxos reais da carteira —
mesmo valor, mesma data, incluindo retirada — em vez de ancorar um único
valor investido na abertura da posição. Ver o docstring da função para o
motivo de comparar cotas em vez de preço rebaseado.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.holdings_history import (
    DividendEvent,
    HoldingEvent,
    PortfolioFlowPoint,
    portfolio_flow_series,
    twr_index_series,
)


@dataclass(frozen=True, slots=True)
class MonthlyPerformancePoint:
    month: date
    """Primeiro dia do mês, usado apenas para ordenação/formatação."""
    ending_value: Decimal
    """Patrimônio VERDADEIRO (quantidade histórica × preço, não mais a
    quantidade de hoje aplicada ao passado) no último ponto observado
    dentro do mês."""
    net_flow: Decimal
    """Soma dos aportes (positivo) e retiradas (negativo) do mês — soma de
    ``PortfolioFlowPoint.net_flow`` dia a dia, nunca o saldo do último dia:
    fluxo é o que aconteceu no mês inteiro, não um estoque para "pegar o
    último"."""
    income_by_kind: Mapping[str, Decimal]
    """Renda creditada ao numerador do retorno dentro do mês, separada por
    ``IncomeKind`` — dividendo, JCP e aluguel de ações. Soma o mês inteiro,
    mesma regra de ``net_flow`` e pelo mesmo motivo. A separação existe
    porque resultado por preço médio e preço de saída, sozinho, mascara
    quanto cada renda rendeu."""
    return_pct: Decimal | None
    """Variação do ÍNDICE TWR entre o fim deste mês e o fim do mês
    anterior — não mais a razão direta entre patrimônios, que um aumento
    de posição contaminaria com o valor do próprio aporte. ``None`` no
    primeiro mês (não há mês anterior para comparar) ou quando o índice do
    mês anterior for zero."""

    @property
    def income(self) -> Decimal:
        """Renda total do mês — o que de fato entrou no numerador do retorno."""
        return sum(self.income_by_kind.values(), Decimal("0"))


@dataclass(frozen=True, slots=True)
class MonthlyPerformanceReport:
    currency: str
    points: list[MonthlyPerformancePoint]
    daily_flows: list[tuple[date, Decimal]]
    """Aportes e retiradas na granularidade DIÁRIA, já avaliados a preço de
    mercado (ver ``app.holdings_history.portfolio_flow_series``). Existe
    para o comparador de índice: ``build_benchmark_shadow_series`` precisa
    da data exata de cada fluxo para comprar a cota certa do benchmark, e o
    ponto mensal já perdeu essa granularidade. Datas sem fluxo ficam de
    fora."""


PERFORMANCE_PERIODS = frozenset({"week", "month", "quarter", "semester", "year", "all"})
"""Períodos aceitos pela rota de performance."""


def normalize_performance_period(value: str | None) -> str:
    """Normaliza o filtro de período na fronteira HTTP.

    ``all`` é o comportamento padrão para que o relatório continue mostrando
    todo o histórico disponível quando a URL não contém filtro.
    """
    return value if value in PERFORMANCE_PERIODS else "all"


def _subtract_calendar_months(value: date, months: int) -> date:
    """Volta meses de calendário sem usar ``float`` nem depender de pacotes extras."""
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def select_performance_period(
    points: Sequence[PortfolioFlowPoint], period: str
) -> list[PortfolioFlowPoint]:
    """Recorta a série diária de fluxo pelo período solicitado, no backend.

    A referência é a última data observada na série
    (``points[-1].observed_date``), e não ``date.today()``. Isso mantém
    relatórios históricos reproduzíveis e permite navegar por dados que
    ainda não tenham sido atualizados até o dia corrente.
    """
    period = normalize_performance_period(period)
    if not points or period == "all":
        return list(points)

    latest_date = points[-1].observed_date
    starts = {
        "week": latest_date - timedelta(days=6),
        "month": _subtract_calendar_months(latest_date, 1),
        "quarter": _subtract_calendar_months(latest_date, 3),
        "semester": _subtract_calendar_months(latest_date, 6),
        "year": _subtract_calendar_months(latest_date, 12),
    }
    start_date = starts[period]
    return [point for point in points if point.observed_date >= start_date]


def _monthly_flow_rows(
    points: Sequence[PortfolioFlowPoint],
    index: Sequence[tuple[date, Decimal]],
) -> list[tuple[date, Decimal, Decimal, dict[str, Decimal], Decimal]]:
    """Reduz a série diária de ``build_monthly_performance`` — pontos de
    fluxo e índice TWR, pareados 1:1 pela posição na lista — a um ponto por
    mês: ``(mês, valor, aporte líquido, provento, índice)``.

    ``valor`` e ``índice`` são ESTOQUE: pegam o ÚLTIMO ponto observado no
    mês. ``aporte`` e ``renda`` são FLUXO: SOMAM todos os pontos do mês
    — tratar os quatro campos do mesmo jeito (só "pegar o último", como
    ``_month_end_values`` faz para uma série simples de valor) creditaria
    apenas o aporte/provento do último dia do mês e perderia o resto. Por
    isso esta redução é própria, e não uma generalização daquela.

    Assume as duas sequências já alinhadas por posição e ordenadas por
    data — garantido por quem monta ``index`` a partir do mesmo ``points``
    (ver ``build_monthly_performance``), nunca a partir de uma série maior
    ou menor.
    """
    ending_value_by_month: dict[date, Decimal] = {}
    index_by_month: dict[date, Decimal] = {}
    flow_by_month: dict[date, Decimal] = {}
    income_by_month: dict[date, dict[str, Decimal]] = {}
    for point, (_, index_value) in zip(points, index, strict=True):
        month = point.observed_date.replace(day=1)
        ending_value_by_month[month] = point.value
        index_by_month[month] = index_value
        flow_by_month[month] = flow_by_month.get(month, Decimal("0")) + point.net_flow
        by_kind = income_by_month.setdefault(month, {})
        for kind, amount in point.income_by_kind.items():
            by_kind[kind] = by_kind.get(kind, Decimal("0")) + amount
    return [
        (
            month,
            ending_value_by_month[month],
            flow_by_month[month],
            income_by_month[month],
            index_by_month[month],
        )
        for month in sorted(ending_value_by_month)
    ]


def build_monthly_performance(
    currency: str,
    events: Sequence[HoldingEvent],
    price_series: Mapping[int, Sequence[tuple[date, Decimal]]],
    dividends: Sequence[DividendEvent] = (),
    period: str = "all",
) -> MonthlyPerformanceReport:
    """Monta o relatório mensal de UMA moeda a partir do extrato bruto.

    ``events`` já chega filtrado para essa moeda — quem separa por moeda é
    a rota, porque moedas nunca são somadas neste app (mesma regra de
    ``app/portfolio.py``); este módulo não tenta descobrir moeda, só
    recebe o rótulo pronto em ``currency``.

    1. ``portfolio_flow_series`` produz a série diária de patrimônio
       verdadeiro + fluxo do dia + provento creditado do dia.
    2. ``select_performance_period`` recorta pelo filtro de período.
    3. ``twr_index_series`` encadeia o índice TWR sobre o RECORTE, não
       sobre ``daily_points`` inteiro — é o que garante que ``index`` e
       ``period_points`` tenham o mesmo tamanho e a mesma data em cada
       posição, premissa que ``_monthly_flow_rows`` exige para parear os
       dois com ``zip(..., strict=True)``.
    4. a série diária resultante (pontos + índice) é reduzida a um ponto
       por mês (``_monthly_flow_rows``), e ``return_pct`` compara o índice
       de cada mês com o do mês anterior.
    """
    daily_points = portfolio_flow_series(events, price_series, dividends)
    period_points = select_performance_period(daily_points, period)
    index = twr_index_series(period_points)

    points: list[MonthlyPerformancePoint] = []
    previous_index: Decimal | None = None
    for month, ending_value, net_flow, month_income, month_index in _monthly_flow_rows(
        period_points, index
    ):
        # `if previous_index` (não `is not None`) descarta de propósito
        # tanto "não há mês anterior" quanto "índice anterior é zero": os
        # dois viram None pelo mesmo teste, porque Decimal("0") é falsy e
        # dividir por ele geraria ZeroDivisionError — mesma convenção de
        # estado definido do restante da contabilidade do app.
        return_pct = month_index / previous_index - 1 if previous_index else None
        points.append(
            MonthlyPerformancePoint(
                month=month,
                ending_value=ending_value,
                net_flow=net_flow,
                income_by_kind=month_income,
                return_pct=return_pct,
            )
        )
        previous_index = month_index
    return MonthlyPerformanceReport(
        currency=currency,
        points=points,
        daily_flows=[
            (point.observed_date, point.net_flow)
            for point in period_points
            if point.net_flow != 0
        ],
    )


def _month_end_values(values: Sequence[tuple[date, Decimal]]) -> list[tuple[date, Decimal]]:
    """Reduz uma série diária de (data, valor) a um ponto por mês: o último
    valor observado dentro de cada mês. Assume ``values`` já ordenado por
    data.

    Serve para séries de "estoque" simples, como o preço (ou a sombra) do
    benchmark em ``align_benchmark_to_points``. O relatório da própria
    carteira tem também campos de "fluxo" (aporte, provento) que somam em
    vez de substituir dentro do mês; para esses, ver ``_monthly_flow_rows``.
    """
    last_by_month: dict[date, Decimal] = {}
    for observed_date, value in values:
        last_by_month[observed_date.replace(day=1)] = value
    return sorted(last_by_month.items())


def align_benchmark_to_points(
    points: Sequence[MonthlyPerformancePoint],
    benchmark_series: Sequence[tuple[date, Decimal]],
) -> list[Decimal | None]:
    """Reduz a série diária de um ticker de referência a um valor por mês (o
    último disponível dentro do mês, mesmo critério de ``_month_end_values``),
    alinhado aos meses de ``points`` para permitir comparar a evolução da
    carteira com a de um índice no gráfico de performance mensal.

    Meses sem cotação do índice viram ``None`` (buraco preservado, nunca
    interpolado) em vez de serem omitidos, para que o índice de cada ponto em
    ``points`` continue correspondendo pela posição na lista resultante.

    Genérica o bastante para alinhar tanto uma série de preço bruto quanto
    o valor de ``build_benchmark_shadow_series`` — ver ``routes.performance``.
    """
    month_ends = dict(_month_end_values(benchmark_series))
    return [month_ends.get(point.month) for point in points]


def build_benchmark_shadow_series(
    flows: Sequence[tuple[date, Decimal]],
    benchmark_series: Sequence[tuple[date, Decimal]],
) -> list[tuple[date, Decimal]]:
    """Curva de "quanto valeria hoje se, em vez de cada ativo comprado, o
    mesmo valor tivesse sido aplicado no benchmark na mesma data" — a
    comparação que de fato responde "eu me saí melhor comprando essas
    ações ou teria sido melhor no índice?" (combinado com o usuário: a
    comparação anterior, carteira em R$ absoluto vs. preço do índice
    rebaseado a %, não fazia sentido porque um novo aporte faz o R$ da
    carteira saltar sem relação com desempenho).

    ``flows`` é a mesma lista de fluxos reais da carteira (``F(d)``, ver
    ``app.holdings_history``) usada para calcular o índice TWR — positivo
    em aporte, negativo em retirada/encerramento. A técnica é acumular
    COTAS hipotéticas do benchmark, em vez de ancorar o valor investido em
    um único ponto (a aproximação antiga — todo o custo médio atual
    ancorado na abertura da posição, sem repartir por compra individual —
    deixa de existir):

        cotas  += fluxo_do_dia / preço_do_benchmark_no_dia
        sombra  = cotas × preço_do_benchmark_no_dia

    Isso resolve três problemas de uma vez: o aporte passa a entrar na
    data certa (não mais só na abertura da posição), a retirada passa a
    REDUZIR a sombra (antes um fluxo `<= 0` era descartado silenciosamente,
    então vender não devolvia nada ao comparador), e o laço vira uma única
    passada casada com o benchmark — O(n+m) — em vez de somar, em cada
    data, todas as âncoras já ativas — O(n·m).

    Regras de borda: fluxo em dia sem cotação do benchmark (fim de semana,
    feriado) vai para o primeiro ponto do benchmark >= a data do fluxo —
    mesma regra de bucket do resto deste trabalho —, o que também cobre
    fluxo anterior à primeira cotação (ancora todos na primeira). Preço
    <= 0 não gera divisão: o fluxo daquele ponto é ignorado, estado
    definido em vez de erro (invariante do ``AGENTS.md``). Cotas podem
    ficar negativas se as retiradas superarem os aportes acumulados — é um
    estado válido (a posição hipotética no benchmark ficou "vendida"), não
    um erro a corrigir. Fluxo com data posterior ao último ponto do
    benchmark não tem bucket nessa malha e fica de fora, mesma convenção
    usada para as demais séries deste trabalho.

    Retorna a série diária de sombra, um ponto por data do benchmark
    (mesma malha de ``benchmark_series``, com "forward fill" implícito das
    cotas entre um fluxo e o próximo). Série de benchmark vazia devolve
    lista vazia.
    """
    ordered_benchmark = sorted(benchmark_series)
    if not ordered_benchmark:
        return []
    ordered_flows = sorted(flows)

    units = Decimal("0")
    flow_index = 0
    shadow: list[tuple[date, Decimal]] = []
    for current_date, current_price in ordered_benchmark:
        while flow_index < len(ordered_flows) and ordered_flows[flow_index][0] <= current_date:
            _, flow_amount = ordered_flows[flow_index]
            if current_price > 0:
                units += flow_amount / current_price
            flow_index += 1
        shadow.append((current_date, units * current_price))
    return shadow
