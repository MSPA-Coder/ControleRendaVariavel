"""Stop gain e breakeven respeitam o lado da posição.

Os dois usavam a fórmula da compra também na venda: o alvo de uma venda com
multiplicador 1,5 ficava 50% ACIMA do custo, justamente onde ela perde, e o
breakeven media a distância como se ganhar fosse o preço subir.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.domain import breakeven_distance, operation_result, stop_gain_price


def test_alvo_da_compra_fica_acima_do_custo():
    assert stop_gain_price("C", Decimal("10"), Decimal("1.5")) == Decimal("15.0")


def test_alvo_da_venda_fica_espelhado_abaixo_do_custo():
    """+50% numa venda é o preço cair à metade."""
    assert stop_gain_price("V", Decimal("10"), Decimal("1.5")) == Decimal("5.0")


def test_alvo_da_venda_nunca_fica_negativo():
    assert stop_gain_price("V", Decimal("10"), Decimal("2.5")) == Decimal("0")


@pytest.mark.parametrize(
    ("lado", "custo", "atual", "esperado"),
    [
        # Compra com ganho: o preço subiu 20% sobre o custo.
        ("C", "10", "12", Decimal("0.2")),
        # Compra com perda: precisa subir 25% para voltar a 10.
        ("C", "10", "8", Decimal("-0.25")),
        # Venda com ganho: o preço caiu 20% sobre o custo.
        ("V", "10", "8", Decimal("0.2")),
        # Venda com perda: precisa cair 1/6 (de 12 para 10) para voltar ao custo.
        ("V", "10", "12", Decimal("10") / Decimal("12") - 1),
    ],
)
def test_breakeven_tem_o_sinal_do_resultado_nos_dois_lados(lado, custo, atual, esperado):
    distancia = breakeven_distance(lado, Decimal(custo), Decimal(atual))

    assert distancia == esperado
    resultado = operation_result(lado, Decimal("1"), Decimal(custo), Decimal(atual))
    assert (distancia > 0) == (resultado > 0)


def test_breakeven_sem_custo_ou_sem_preco_nao_se_aplica():
    assert breakeven_distance("C", Decimal("0"), Decimal("10")) is None
    assert breakeven_distance("V", Decimal("10"), Decimal("0")) is None


def test_resultado_e_bruto_sem_fator_de_custo():
    """O antigo modo L multiplicava por 0,9996 e, numa perda, a melhorava."""
    assert operation_result("C", Decimal("100"), Decimal("10"), Decimal("12")) == Decimal("200")
    assert operation_result("C", Decimal("100"), Decimal("10"), Decimal("8")) == Decimal("-200")
    assert operation_result("V", Decimal("100"), Decimal("10"), Decimal("8")) == Decimal("200")
