"""Regra da conferência por pregão (pura, sem banco nem rede)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.executions.conferencia import (
    DIFFERENCE,
    MATCH,
    NO_CB,
    ONLY_CB,
    ONLY_CRV,
    Movement,
    compare,
    tolerance_from_config,
)

D = Decimal

#: Os 13 pregões do estudo, com valores SINTÉTICOS de mesma ordem de grandeza e
#: de mesma diferença (CB - myProfit entre 0,09 e 3,59), em sinais variados.
#: (pregão, CB, CRV)
TREZE_PREGOES = [
    (date(2026, 1, 27), D("12345.60"), D("12345.85")),
    (date(2026, 1, 28), D("1500.10"), D("1500.19")),
    (date(2026, 1, 29), D("310.20"), D("310.29")),
    (date(2026, 2, 11), D("2100.30"), D("2100.49")),
    (date(2026, 2, 13), D("3000.40"), D("3000.60")),
    (date(2026, 3, 20), D("-28000.50"), D("-27998.18")),
    (date(2026, 3, 26), D("-15000.00"), D("-15001.99")),
    (date(2026, 5, 11), D("420.70"), D("420.79")),
    (date(2026, 6, 2), D("-10100.10"), D("-10101.09")),
    (date(2026, 6, 24), D("60.51"), D("58.71")),
    (date(2026, 8, 12), D("-900.30"), D("-899.55")),
    (date(2026, 8, 13), D("2200.20"), D("2200.29")),
    (date(2026, 8, 28), D("-2300.00"), D("-2303.59")),
]


def _lados(pares):
    cb = [Movement("Genial", dia, "BRL", valor) for dia, valor, _ in pares]
    crv = [Movement("Genial", dia, "BRL", valor, "B3_GENIAL_X") for dia, _, valor in pares]
    return cb, crv


def test_os_treze_pregoes_da_genial_conferem_dentro_da_tolerancia_padrao():
    cb, crv = _lados(TREZE_PREGOES)

    linhas = compare(cb, crv)

    assert len(linhas) == 13
    assert {linha.state for linha in linhas} == {MATCH}
    maior = max(abs(linha.implicit_cost) for linha in linhas)
    assert maior == D("3.59")


def test_o_custo_implicito_e_crv_menos_cb_com_sinal():
    cb, crv = _lados([(date(2026, 3, 20), D("-100.00"), D("-97.68")), (date(2026, 6, 24), D("44.51"), D("42.71"))])

    por_dia = {linha.pregao: linha.implicit_cost for linha in compare(cb, crv)}

    assert por_dia[date(2026, 3, 20)] == D("2.32")
    assert por_dia[date(2026, 6, 24)] == D("-1.80")


def test_diferenca_acima_da_tolerancia_e_classificada_como_diferenca():
    cb, crv = _lados([(date(2026, 1, 27), D("100.00"), D("105.01"))])

    assert compare(cb, crv)[0].state == DIFFERENCE


def test_exatamente_a_tolerancia_ainda_confere():
    cb, crv = _lados([(date(2026, 1, 27), D("100.00"), D("105.00"))])

    assert compare(cb, crv)[0].state == MATCH
    assert compare(cb, crv, tolerance=D("4.99"))[0].state == DIFFERENCE


def test_so_no_cb_e_so_no_crv():
    cb = [Movement("Genial", date(2026, 2, 2), "BRL", D("10.00"))]
    crv = [Movement("Genial", date(2026, 2, 3), "BRL", D("20.00"), "DOC")]

    estados = {linha.pregao: (linha.state, linha.cb_amount, linha.crv_amount) for linha in compare(cb, crv)}

    assert estados[date(2026, 2, 2)] == (ONLY_CB, D("10.00"), None)
    assert estados[date(2026, 2, 3)] == (ONLY_CRV, None, D("20.00"))


def test_varios_lancamentos_do_mesmo_pregao_somam_nos_dois_lados():
    cb = [Movement("Avenue", date(2026, 2, 2), "BRL", D("10.00")), Movement("Avenue", date(2026, 2, 2), "BRL", D("-4.00"))]
    crv = [Movement("Avenue", date(2026, 2, 2), "BRL", D("6.50"), "A"), Movement("Avenue", date(2026, 2, 2), "BRL", D("0.50"), "B")]

    linha = compare(cb, crv)[0]

    assert (linha.cb_amount, linha.crv_amount, linha.state) == (D("6.00"), D("7.00"), MATCH)
    assert linha.documents == ("A", "B")


def test_corretoras_e_moedas_diferentes_nunca_se_somam():
    cb = [Movement("Genial", date(2026, 2, 2), "BRL", D("10.00")), Movement("XP", date(2026, 2, 2), "BRL", D("10.00"))]
    crv = [Movement("Genial", date(2026, 2, 2), "USD", D("10.00"), "A")]

    estados = {(linha.broker, linha.currency): linha.state for linha in compare(cb, crv)}

    assert estados == {("Genial", "BRL"): ONLY_CB, ("XP", "BRL"): ONLY_CB, ("Genial", "USD"): ONLY_CRV}


def test_sem_cb_disponivel_nada_e_chamado_de_so_no_crv():
    crv = [Movement("Genial", date(2026, 2, 3), "BRL", D("20.00"), "DOC")]

    linhas = compare([], crv, cb_available=False)

    assert [(linha.state, linha.cb_amount, linha.implicit_cost) for linha in linhas] == [(NO_CB, None, None)]


def test_linhas_saem_em_ordem_de_pregao():
    cb, crv = _lados([(date(2026, 8, 1), D("1"), D("1")), (date(2026, 1, 1), D("1"), D("1"))])

    assert [linha.pregao for linha in compare(cb, crv)] == [date(2026, 1, 1), date(2026, 8, 1)]


@pytest.mark.parametrize(("bruto", "esperado"), [(None, D("5.00")), ("", D("5.00")), ("2.50", D("2.50")), ("0", D("0"))])
def test_tolerancia_configuravel_com_padrao_de_cinco_reais(bruto, esperado):
    assert tolerance_from_config({"CONFERENCIA_TOLERANCIA": bruto}) == esperado


@pytest.mark.parametrize("bruto", ["-1", "abc", "NaN"])
def test_tolerancia_invalida_e_recusada(bruto):
    with pytest.raises(ValueError, match="CONFERENCIA_TOLERANCIA"):
        tolerance_from_config({"CONFERENCIA_TOLERANCIA": bruto})
