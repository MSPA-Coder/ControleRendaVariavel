"""Redução mensal e comparador de índice de ``app.monthly_performance``.

A matemática diária do TWR já está coberta em ``tests/test_holdings_history.py``
e não é retestada aqui. O que este arquivo protege é o que a redução a um
ponto por mês pode errar sozinha — misturar estoque com fluxo — e o
comparador de benchmark.

Mesmo critério de comparação do arquivo da fundação: somas e produtos de
``Decimal`` comparam por igualdade; o que passa por divisão compara com
tolerância explícita (``_close``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.holdings_history import DividendEvent, HoldingEvent
from app.monthly_performance import (
    MonthlyPerformancePoint,
    align_benchmark_to_points,
    build_benchmark_shadow_series,
    build_monthly_performance,
)


def _event(
    occurred_on: date,
    *,
    ticker_id: int = 1,
    resulting_signed_quantity: Decimal = Decimal("0"),
    position_key: tuple[str, int] = ("stock", 1),
) -> HoldingEvent:
    return HoldingEvent(
        occurred_on=occurred_on,
        ticker_id=ticker_id,
        resulting_signed_quantity=resulting_signed_quantity,
        position_key=position_key,
    )


def _close(actual: Decimal, expected: Decimal, tolerance: Decimal = Decimal("0.0001")) -> bool:
    return abs(actual - expected) < tolerance


def _by_month(report) -> dict[date, MonthlyPerformancePoint]:
    return {point.month: point for point in report.points}


# --- Aumento no meio: a regressão que motivou o trabalho inteiro -----------


def test_aumento_de_posicao_nao_aparece_como_retorno_no_mes():
    # 100 acoes em janeiro, mais 100 em junho, preco constante em 10.
    # O patrimonio dobra em junho, mas nada disso e desempenho: o mes tem
    # que fechar com retorno zero e o aporte visivel em coluna propria.
    events = [
        _event(date(2026, 1, 5), resulting_signed_quantity=Decimal("100")),
        _event(date(2026, 6, 5), resulting_signed_quantity=Decimal("200")),
    ]
    price_series = {
        1: [
            (date(2026, 1, 5), Decimal("10")),
            (date(2026, 1, 30), Decimal("10")),
            (date(2026, 5, 29), Decimal("10")),
            (date(2026, 6, 5), Decimal("10")),
            (date(2026, 6, 30), Decimal("10")),
        ]
    }

    months = _by_month(build_monthly_performance("BRL", events, price_series))

    janeiro = months[date(2026, 1, 1)]
    assert janeiro.ending_value == Decimal("1000")
    assert janeiro.return_pct is None  # primeiro mes nao tem anterior

    junho = months[date(2026, 6, 1)]
    assert junho.ending_value == Decimal("2000")  # patrimonio real dobrou
    assert junho.net_flow == Decimal("1000")  # e o aporte explica o salto
    assert _close(junho.return_pct, Decimal("0")), junho.return_pct


# --- Estoque vs. fluxo: a armadilha específica da redução mensal -----------


def test_dois_aportes_no_mesmo_mes_somam_e_o_valor_pega_o_ultimo():
    # `ending_value` e ESTOQUE (ultimo ponto do mes); `net_flow` e
    # `dividends` sao FLUXO (somam o mes). Tratar os quatro campos como
    # "pega o ultimo" creditaria so o aporte do dia 20 e perderia o do dia 2.
    events = [
        _event(date(2026, 3, 2), resulting_signed_quantity=Decimal("100")),
        _event(date(2026, 3, 20), resulting_signed_quantity=Decimal("150")),
    ]
    price_series = {
        1: [
            (date(2026, 3, 2), Decimal("10")),
            (date(2026, 3, 20), Decimal("10")),
            (date(2026, 3, 31), Decimal("10")),
        ]
    }
    dividends = [
        DividendEvent(payment_date=date(2026, 3, 20), ticker_id=1, amount=Decimal("20")),
        DividendEvent(payment_date=date(2026, 3, 31), ticker_id=1, amount=Decimal("10")),
    ]

    months = _by_month(build_monthly_performance("BRL", events, price_series, dividends))
    marco = months[date(2026, 3, 1)]

    assert marco.ending_value == Decimal("1500")  # 150 acoes a 10, ultimo ponto
    assert marco.net_flow == Decimal("1500")  # 1000 + 500, nao apenas 500
    assert marco.income == Decimal("30")  # 20 + 10, nao apenas 10


# --- Encadeamento mês a mês -------------------------------------------------


def test_retorno_mensal_encadeia_a_variacao_de_preco_sem_fluxo():
    events = [
        _event(date(2026, 1, 2), resulting_signed_quantity=Decimal("100")),
    ]
    price_series = {
        1: [
            (date(2026, 1, 2), Decimal("10")),
            (date(2026, 1, 31), Decimal("10")),
            (date(2026, 2, 27), Decimal("11")),  # +10%
            (date(2026, 3, 31), Decimal("12.10")),  # +10% de novo
        ]
    }

    months = _by_month(build_monthly_performance("BRL", events, price_series))

    assert months[date(2026, 1, 1)].return_pct is None
    assert _close(months[date(2026, 2, 1)].return_pct, Decimal("0.1"))
    assert _close(months[date(2026, 3, 1)].return_pct, Decimal("0.1"))


# --- Proventos ---------------------------------------------------------------


def test_provento_entra_no_retorno_do_mes_sem_mexer_no_patrimonio():
    # Sem conta caixa no app: o provento nao soma ao patrimonio, ele entra no
    # numerador do retorno. Preco parado, 50 recebidos sobre 1000 = +5%.
    events = [
        _event(date(2026, 1, 2), resulting_signed_quantity=Decimal("100")),
    ]
    price_series = {
        1: [
            (date(2026, 1, 2), Decimal("10")),
            (date(2026, 1, 31), Decimal("10")),
            (date(2026, 2, 27), Decimal("10")),
        ]
    }
    dividends = [DividendEvent(payment_date=date(2026, 2, 10), ticker_id=1, amount=Decimal("50"))]

    fevereiro = _by_month(build_monthly_performance("BRL", events, price_series, dividends))[
        date(2026, 2, 1)
    ]

    assert fevereiro.ending_value == Decimal("1000")  # patrimonio nao muda
    assert fevereiro.income == Decimal("50")
    assert _close(fevereiro.return_pct, Decimal("0.05")), fevereiro.return_pct


# --- Filtro de período -------------------------------------------------------


def test_periodo_usa_a_ultima_data_da_serie_e_nao_a_data_de_hoje():
    # Datas deliberadamente antigas: se o recorte usasse `date.today()`, o
    # filtro "month" devolveria vazio e este teste quebraria sozinho com o
    # passar do tempo. A referencia e a ultima cotacao existente, o que
    # mantem relatorios historicos reproduziveis.
    events = [
        _event(date(2020, 1, 2), resulting_signed_quantity=Decimal("100")),
    ]
    price_series = {
        1: [
            (date(2020, 1, 2), Decimal("10")),
            (date(2020, 6, 30), Decimal("10")),
            (date(2020, 12, 31), Decimal("10")),
        ]
    }

    todos = build_monthly_performance("BRL", events, price_series, (), "all")
    assert [point.month for point in todos.points] == [
        date(2020, 1, 1),
        date(2020, 6, 1),
        date(2020, 12, 1),
    ]

    ultimo_mes = build_monthly_performance("BRL", events, price_series, (), "month")
    assert [point.month for point in ultimo_mes.points] == [date(2020, 12, 1)]


def test_sem_eventos_ou_sem_cotacao_devolve_relatorio_vazio():
    assert build_monthly_performance("BRL", [], {}).points == []

    events = [_event(date(2026, 1, 2), resulting_signed_quantity=Decimal("100"))]
    assert build_monthly_performance("BRL", events, {}).points == []


# --- Comparador de índice -----------------------------------------------------


def test_sombra_acumula_cotas_pelo_preco_de_cada_aporte():
    # 1000 a 10 = 100 cotas; mais 1000 a 20 = 50 cotas; 150 cotas a 20 = 3000.
    flows = [(date(2026, 1, 5), Decimal("1000")), (date(2026, 2, 5), Decimal("1000"))]
    benchmark = [
        (date(2026, 1, 5), Decimal("10")),
        (date(2026, 2, 5), Decimal("20")),
        (date(2026, 3, 5), Decimal("20")),
    ]

    shadow = dict(build_benchmark_shadow_series(flows, benchmark))

    assert shadow[date(2026, 1, 5)] == Decimal("1000")
    assert shadow[date(2026, 2, 5)] == Decimal("3000")
    assert shadow[date(2026, 3, 5)] == Decimal("3000")  # sem fluxo novo, cotas param


def test_retirada_reduz_a_sombra():
    # Regressao: o modelo antigo descartava fluxo <= 0, entao vender nao
    # devolvia nada ao comparador e a curva hipotetica so subia.
    flows = [(date(2026, 1, 5), Decimal("1000")), (date(2026, 2, 5), Decimal("-500"))]
    benchmark = [(date(2026, 1, 5), Decimal("10")), (date(2026, 2, 5), Decimal("10"))]

    shadow = dict(build_benchmark_shadow_series(flows, benchmark))

    assert shadow[date(2026, 1, 5)] == Decimal("1000")
    assert shadow[date(2026, 2, 5)] == Decimal("500")  # 100 cotas - 50 cotas


def test_sombra_ancora_fluxo_sem_cotacao_no_proximo_ponto_e_ignora_o_que_passa_do_fim():
    flows = [
        (date(2025, 12, 1), Decimal("100")),  # antes da primeira cotacao
        (date(2026, 1, 3), Decimal("200")),  # sabado, sem pregao
        (date(2026, 6, 1), Decimal("999")),  # depois do ultimo ponto
    ]
    benchmark = [(date(2026, 1, 2), Decimal("10")), (date(2026, 1, 5), Decimal("10"))]

    shadow = dict(build_benchmark_shadow_series(flows, benchmark))

    assert shadow[date(2026, 1, 2)] == Decimal("100")  # 10 cotas
    assert shadow[date(2026, 1, 5)] == Decimal("300")  # + 20 cotas do sabado
    assert len(shadow) == 2  # o fluxo de junho nao criou ponto nenhum


def test_sombra_com_preco_zero_ou_benchmark_vazio_nao_quebra():
    flows = [(date(2026, 1, 5), Decimal("1000"))]

    assert build_benchmark_shadow_series(flows, []) == []

    # Preco zero nao pode gerar divisao: estado definido, nao excecao.
    shadow = build_benchmark_shadow_series(flows, [(date(2026, 1, 5), Decimal("0"))])
    assert shadow == [(date(2026, 1, 5), Decimal("0"))]


# --- Alinhamento do índice aos meses -------------------------------------------


def test_mes_sem_cotacao_do_indice_vira_none_e_o_alinhamento_e_posicional():
    # O grafico casa cada mes com o valor pela POSICAO na lista: um buraco
    # precisa virar None, nunca ser omitido, senao todo o resto desloca.
    points = [
        MonthlyPerformancePoint(
            month=month,
            ending_value=Decimal("0"),
            net_flow=Decimal("0"),
            income_by_kind={},
            return_pct=None,
        )
        for month in (date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1))
    ]
    benchmark = [(date(2026, 1, 31), Decimal("10")), (date(2026, 3, 31), Decimal("30"))]

    assert align_benchmark_to_points(points, benchmark) == [Decimal("10"), None, Decimal("30")]


def test_as_tres_rendas_somam_no_retorno_e_ficam_separadas_por_tipo():
    """Dividendo, JCP e aluguel entram no retorno pelo mesmo caminho, mas o
    relatorio precisa dizer quanto cada um rendeu -- e o motivo de existir
    `income_by_kind`: resultado por preco medio e preco de saida, sozinho,
    mascara a renda.

    Tambem trava a nao-contagem-dupla: desde que `quote_history` passou a
    gravar o `close` nominal, o preco nao embute renda nenhuma, entao as tres
    precisam ser creditadas aqui.
    """
    events = [_event(date(2026, 1, 2), resulting_signed_quantity=Decimal("100"))]
    price_series = {
        1: [
            (date(2026, 1, 2), Decimal("10")),
            (date(2026, 1, 31), Decimal("10")),
            (date(2026, 2, 27), Decimal("10")),
        ]
    }
    rendas = [
        DividendEvent(date(2026, 2, 5), 1, Decimal("30"), "dividendo"),
        DividendEvent(date(2026, 2, 10), 1, Decimal("15"), "jcp"),
        DividendEvent(date(2026, 2, 20), 1, Decimal("5"), "aluguel"),
    ]

    fevereiro = _by_month(build_monthly_performance("BRL", events, price_series, rendas))[
        date(2026, 2, 1)
    ]

    assert fevereiro.income_by_kind == {
        "dividendo": Decimal("30"),
        "jcp": Decimal("15"),
        "aluguel": Decimal("5"),
    }
    assert fevereiro.income == Decimal("50")
    # 50 sobre 1000 = +5%, sem o patrimonio mudar (nao ha conta caixa).
    assert fevereiro.ending_value == Decimal("1000")
    assert _close(fevereiro.return_pct, Decimal("0.05")), fevereiro.return_pct
