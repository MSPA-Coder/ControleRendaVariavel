"""Matemática pura de ``app.holdings_history`` — sem app, sem banco, sem
fixtures: cálculo de domínio se testa com números conferidos à mão.

O caso que mais importa aqui é o aumento no meio do histórico — é o motivo
de o módulo existir (ver o docstring de ``app.holdings_history``). Quantidade
histórica errada faz o aporte aparecer como retorno;
``test_aumento_no_meio_nao_vira_retorno`` é a regressão que protege
exatamente isso.

Os valores esperados usam números redondos. Somas e produtos de ``Decimal``
comparam por igualdade direta (``==``); retornos e índices, que passam por
divisão, comparam com tolerância explícita (``_close``) — mesmo quando a
conta do caso fecha exata, para não depender disso.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.holdings_history import (
    DividendEvent,
    HoldingEvent,
    QuantityTimeline,
    portfolio_flow_series,
    prorate_dividends,
    twr_index_series,
)


def _event(
    occurred_on: date,
    *,
    ticker_id: int = 1,
    resulting_signed_quantity: Decimal = Decimal("0"),
    position_key: tuple[str, int] = ("stock", 1),
) -> HoldingEvent:
    """Reduz o boilerplate dos testes: a maioria só varia data e
    quantidade, com ticker e posição únicos por padrão."""
    return HoldingEvent(
        occurred_on=occurred_on,
        ticker_id=ticker_id,
        resulting_signed_quantity=resulting_signed_quantity,
        position_key=position_key,
    )


def _close(actual: Decimal, expected: Decimal, tolerance: Decimal = Decimal("0.0001")) -> bool:
    """Tolerância explícita para comparação de Decimal derivado de divisão
    — ver o docstring do módulo."""
    return abs(actual - expected) < tolerance


# --- QuantityTimeline -------------------------------------------------------


def test_quantity_at_antes_entre_e_depois_dos_eventos():
    events = [
        _event(date(2026, 1, 10), resulting_signed_quantity=Decimal("100")),
        _event(date(2026, 3, 10), resulting_signed_quantity=Decimal("150")),
    ]
    timeline = QuantityTimeline(events)

    assert timeline.quantity_at(1, date(2026, 1, 1)) == Decimal("0")  # antes do primeiro
    assert timeline.quantity_at(1, date(2026, 1, 10)) == Decimal("100")  # no dia, inclusive
    assert timeline.quantity_at(1, date(2026, 2, 1)) == Decimal("100")  # entre eventos
    assert timeline.quantity_at(1, date(2026, 3, 10)) == Decimal("150")  # no segundo, inclusive
    assert timeline.quantity_at(1, date(2026, 12, 31)) == Decimal("150")  # depois do último
    assert timeline.quantity_at(999, date(2026, 6, 1)) == Decimal("0")  # ticker sem evento algum


def test_ticker_ids_sao_os_tickers_com_evento_ordenados():
    events = [
        _event(date(2026, 1, 1), ticker_id=5, position_key=("stock", 10)),
        _event(date(2026, 1, 1), ticker_id=2, position_key=("stock", 11)),
        _event(date(2026, 1, 1), ticker_id=8, position_key=("option", 12)),
    ]
    timeline = QuantityTimeline(events)

    assert timeline.ticker_ids == [2, 5, 8]


def test_quantity_at_soma_varias_posicoes_do_mesmo_ticker():
    # Uma acao e uma opcao do mesmo ticker, ids de sequencia colidentes de
    # proposito (Position e OptionPosition tem sequencias independentes).
    events = [
        _event(date(2026, 1, 1), resulting_signed_quantity=Decimal("100"), position_key=("stock", 7)),
        _event(
            date(2026, 1, 1),
            resulting_signed_quantity=Decimal("20"),
            position_key=("option", 7),
        ),
    ]
    timeline = QuantityTimeline(events)

    assert timeline.quantity_at(1, date(2026, 1, 1)) == Decimal("120")


# --- Aumento no meio: o ponto inteiro deste trabalho ------------------------


def test_aumento_no_meio_nao_vira_retorno():
    events = [
        _event(date(2026, 1, 5), resulting_signed_quantity=Decimal("100")),
        _event(date(2026, 6, 5), resulting_signed_quantity=Decimal("200")),
    ]
    price_series = {
        1: [
            (date(2026, 1, 5), Decimal("10")),
            (date(2026, 1, 31), Decimal("10")),
            (date(2026, 5, 31), Decimal("10")),
            (date(2026, 6, 5), Decimal("10")),
            (date(2026, 6, 6), Decimal("10")),
        ]
    }

    points = portfolio_flow_series(events, price_series)
    by_date = {point.observed_date: point for point in points}

    # antes do aumento: 100 acoes a 10
    assert by_date[date(2026, 5, 31)].value == Decimal("1000")
    # no dia do aumento: quantidade e valor dobram, e o aporte fica visivel
    assert by_date[date(2026, 6, 5)].value == Decimal("2000")
    assert by_date[date(2026, 6, 5)].net_flow == Decimal("1000")

    index = dict(twr_index_series(points))
    # o salto de 1000 para 2000 e inteiramente explicado pelo aporte de
    # 1000: nao sobra nada para o indice registrar como retorno.
    assert _close(index[date(2026, 6, 5)], Decimal("1")), index[date(2026, 6, 5)]
    assert _close(index[date(2026, 6, 6)], Decimal("1")), index[date(2026, 6, 6)]


# --- Variação de preço sem fluxo --------------------------------------------


def test_preco_sobe_dez_por_cento_sem_fluxo_indice_sobe_dez_por_cento():
    events = [
        _event(date(2026, 1, 1), resulting_signed_quantity=Decimal("100")),
    ]
    price_series = {
        1: [
            (date(2026, 1, 1), Decimal("100")),
            (date(2026, 1, 31), Decimal("110")),
        ]
    }

    points = portfolio_flow_series(events, price_series)
    index = dict(twr_index_series(points))

    assert _close(index[date(2026, 1, 31)], Decimal("1.1")), index[date(2026, 1, 31)]


# --- Fluxo negativo (encerramento parcial) ----------------------------------


def test_encerramento_parcial_nao_vira_retorno():
    events = [
        _event(date(2026, 1, 1), resulting_signed_quantity=Decimal("200")),
        # DECREASE: fluxo negativo, saida ao mesmo preco de entrada (sem
        # ganho nem perda real na saida).
        _event(date(2026, 2, 1), resulting_signed_quantity=Decimal("100")),
    ]
    price_series = {
        1: [
            (date(2026, 1, 1), Decimal("10")),
            (date(2026, 2, 1), Decimal("10")),
        ]
    }

    points = portfolio_flow_series(events, price_series)
    by_date = {point.observed_date: point for point in points}
    assert by_date[date(2026, 2, 1)].value == Decimal("1000")  # 100 acoes a 10
    assert by_date[date(2026, 2, 1)].net_flow == Decimal("-1000")

    index = dict(twr_index_series(points))
    assert _close(index[date(2026, 2, 1)], Decimal("1")), index[date(2026, 2, 1)]


# --- Proventos ---------------------------------------------------------------


def test_provento_sem_variacao_de_preco_retorno_igual_a_d_sobre_v():
    events = [
        _event(date(2026, 1, 1), resulting_signed_quantity=Decimal("100")),
    ]
    price_series = {
        1: [
            (date(2026, 1, 1), Decimal("10")),
            (date(2026, 2, 1), Decimal("10")),
        ]
    }
    dividends = [DividendEvent(payment_date=date(2026, 2, 1), ticker_id=1, amount=Decimal("50"))]

    points = portfolio_flow_series(events, price_series, dividends)
    by_date = {point.observed_date: point for point in points}
    assert by_date[date(2026, 2, 1)].income == Decimal("50")

    index = dict(twr_index_series(points))
    # D/V = 50/1000 = 0.05
    assert _close(index[date(2026, 2, 1)], Decimal("1.05")), index[date(2026, 2, 1)]


def test_prorate_dividends_metade_da_quantidade_credita_metade_e_total_zero_descarta():
    total_events = [
        _event(date(2026, 1, 1), ticker_id=1, resulting_signed_quantity=Decimal("100"), position_key=("stock", 1)),
        _event(date(2026, 1, 1), ticker_id=2, resulting_signed_quantity=Decimal("100"), position_key=("stock", 2)),
        # ticker 2 encerra antes do pagamento: quantidade total zero na data.
        _event(date(2026, 1, 15), ticker_id=2, resulting_signed_quantity=Decimal("0"), position_key=("stock", 2)),
    ]
    filtered_events = [
        _event(date(2026, 1, 1), ticker_id=1, resulting_signed_quantity=Decimal("50"), position_key=("stock", 1)),
    ]
    total = QuantityTimeline(total_events)
    filtered = QuantityTimeline(filtered_events)

    dividends = [
        DividendEvent(payment_date=date(2026, 2, 1), ticker_id=1, amount=Decimal("80")),
        DividendEvent(payment_date=date(2026, 2, 1), ticker_id=2, amount=Decimal("30")),
    ]

    result = prorate_dividends(dividends, filtered, total)

    assert len(result) == 1
    assert result[0].ticker_id == 1
    assert result[0].amount == Decimal("40")  # metade de 80


def test_prorate_dividends_recorte_zero_tambem_descarta():
    total_events = [_event(date(2026, 1, 1), resulting_signed_quantity=Decimal("100"))]
    filtered_events: list[HoldingEvent] = []  # o recorte nunca deteve o ticker
    total = QuantityTimeline(total_events)
    filtered = QuantityTimeline(filtered_events)
    dividends = [DividendEvent(payment_date=date(2026, 2, 1), ticker_id=1, amount=Decimal("80"))]

    assert prorate_dividends(dividends, filtered, total) == []


def test_prorate_dividends_razao_maior_que_um_e_truncada_em_um():
    # Nao deveria acontecer na pratica (o recorte e subconjunto do total),
    # mas o truncamento e o cinto de seguranca documentado no contrato.
    filtered_events = [_event(date(2026, 1, 1), resulting_signed_quantity=Decimal("100"))]
    total_events = [_event(date(2026, 1, 1), resulting_signed_quantity=Decimal("40"))]
    filtered = QuantityTimeline(filtered_events)
    total = QuantityTimeline(total_events)
    dividends = [DividendEvent(payment_date=date(2026, 2, 1), ticker_id=1, amount=Decimal("80"))]

    result = prorate_dividends(dividends, filtered, total)

    assert result[0].amount == Decimal("80")  # truncado em 100%, nao 200%


def test_prorate_dividends_preserva_ordem_de_entrada():
    events = [_event(date(2026, 1, 1), resulting_signed_quantity=Decimal("10"))]
    timeline = QuantityTimeline(events)
    # Datas de pagamento fora de ordem cronologica de proposito: a saida
    # segue a ordem de ENTRADA, nao reordena por data.
    dividends = [
        DividendEvent(payment_date=date(2026, 3, 1), ticker_id=1, amount=Decimal("5")),
        DividendEvent(payment_date=date(2026, 2, 1), ticker_id=1, amount=Decimal("7")),
    ]

    result = prorate_dividends(dividends, timeline, timeline)

    assert [event.amount for event in result] == [Decimal("5"), Decimal("7")]


# --- Base zero: divisão por zero produz estado definido ---------------------


def test_base_zero_repete_indice_sem_excecao():
    # Cotacao existe desde 1/1, mas a posicao so abre em 1/2: o primeiro
    # ponto da malha tem valor zero (quantidade zero), o que zera a base do
    # retorno seguinte.
    events = [
        _event(date(2026, 1, 2), resulting_signed_quantity=Decimal("100")),
    ]
    price_series = {
        1: [
            (date(2026, 1, 1), Decimal("10")),
            (date(2026, 1, 2), Decimal("11")),
        ]
    }

    points = portfolio_flow_series(events, price_series)
    by_date = {point.observed_date: point for point in points}
    assert by_date[date(2026, 1, 1)].value == Decimal("0")

    index = twr_index_series(points)  # nao pode levantar ZeroDivisionError

    assert index[0] == (date(2026, 1, 1), Decimal("1"))
    assert index[1] == (date(2026, 1, 2), Decimal("1"))  # indice anterior repetido


# --- Malha de datas: dia sem cotação e evento fora do intervalo ------------


def test_evento_em_dia_sem_cotacao_e_absorvido_e_evento_apos_a_malha_fica_de_fora():
    events = [
        _event(date(2026, 1, 2), resulting_signed_quantity=Decimal("100")),
        # sabado, sem pregao: precisa ser absorvido no proximo ponto da malha.
        _event(
            date(2026, 1, 3),
            resulting_signed_quantity=Decimal("150"),
            position_key=("stock", 2),
        ),
        # depois do ultimo ponto da malha: fica de fora, nao aparece em ponto nenhum.
        _event(
            date(2026, 1, 10),
            resulting_signed_quantity=Decimal("999"),
            position_key=("stock", 3),
        ),
    ]
    price_series = {
        1: [
            (date(2026, 1, 2), Decimal("10")),  # sexta
            (date(2026, 1, 5), Decimal("10")),  # segunda seguinte
        ]
    }

    points = portfolio_flow_series(events, price_series)
    by_date = {point.observed_date: point for point in points}

    assert list(by_date) == [date(2026, 1, 2), date(2026, 1, 5)]
    # Fluxo avaliado a preco de MERCADO da data: 100 acoes a 10 na sexta,
    # mais 150 a 10 na segunda (o sabado nao tem pregao para ancorar).
    assert by_date[date(2026, 1, 2)].net_flow == Decimal("1000")
    assert by_date[date(2026, 1, 5)].net_flow == Decimal("1500")  # sabado absorvido aqui
    # 100 (posicao 1) + 150 (posicao 2) a 10; a posicao 3 (1/10) nao entrou.
    assert by_date[date(2026, 1, 5)].value == Decimal("2500")

    total_net_flow = sum((point.net_flow for point in points), Decimal("0"))
    # 1000 + 1500; o evento de 1/10 nao soma em lugar nenhum, e o total bate
    # com o patrimonio porque o preco nao se moveu em nenhum momento.
    assert total_net_flow == Decimal("2500")


# --- Posição vendida ----------------------------------------------------------


def test_posicao_vendida_queda_de_preco_da_retorno_positivo():
    events = [
        _event(date(2026, 1, 1), resulting_signed_quantity=Decimal("-100")),
    ]
    price_series = {
        1: [
            (date(2026, 1, 1), Decimal("10")),
            (date(2026, 2, 1), Decimal("8")),  # preco caiu: bom para quem esta vendido
        ]
    }

    points = portfolio_flow_series(events, price_series)
    by_date = {point.observed_date: point for point in points}
    assert by_date[date(2026, 1, 1)].value == Decimal("-1000")
    assert by_date[date(2026, 2, 1)].value == Decimal("-800")

    index = dict(twr_index_series(points))
    # +20%: sem o abs() no denominador o sinal do retorno inverteria.
    assert _close(index[date(2026, 2, 1)], Decimal("1.2")), index[date(2026, 2, 1)]


# --- Casos vazios --------------------------------------------------------------


def test_series_vazias_nao_quebram():
    assert portfolio_flow_series([], {}) == []
    assert twr_index_series([]) == []
    assert prorate_dividends([], QuantityTimeline([]), QuantityTimeline([])) == []


def test_preco_pago_divergente_da_cotacao_nao_contamina_o_retorno():
    """Regressao do artefato encontrado nos dados reais: dez posicoes antigas
    cadastradas em lote, com `opened_on` sendo a data do CADASTRO e custo
    nominal, contra um `quote_history` de fechamento ajustado. Onze dias de
    aporte assim injetavam ~32% de queda falsa no indice.

    Aqui a posicao entra valendo 200 (20 acoes a 10) num dia em que o preco
    pago teria sido outro qualquer: como o fluxo e avaliado a mercado, o
    retorno do dia e zero, e nenhum erro de cadastro alcanca a serie.
    """
    events = [
        _event(date(2026, 1, 5), resulting_signed_quantity=Decimal("100")),
        _event(date(2026, 2, 5), resulting_signed_quantity=Decimal("120"), position_key=("stock", 1)),
    ]
    price_series = {
        1: [
            (date(2026, 1, 5), Decimal("10")),
            (date(2026, 2, 5), Decimal("10")),
            (date(2026, 2, 6), Decimal("10")),
        ]
    }

    points = portfolio_flow_series(events, price_series)
    by_date = {point.observed_date: point for point in points}

    # 20 acoes novas a 10 = 200, o valor de mercado do que entrou.
    assert by_date[date(2026, 2, 5)].net_flow == Decimal("200")
    assert by_date[date(2026, 2, 5)].value == Decimal("1200")

    index = dict(twr_index_series(points))
    assert _close(index[date(2026, 2, 5)], Decimal("1")), index[date(2026, 2, 5)]
    assert _close(index[date(2026, 2, 6)], Decimal("1")), index[date(2026, 2, 6)]
