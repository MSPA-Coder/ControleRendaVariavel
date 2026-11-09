"""Regra da conferência por pregão (pura, sem banco nem rede)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.executions.carteira import compare_holdings, settled_cutoff
from app.executions.conferencia import (
    AWAITING_STATEMENT,
    DIFFERENCE,
    MATCH,
    NO_CB,
    ONLY_CB,
    ONLY_CRV,
    STATE_LABELS,
    Movement,
    Trade,
    compare,
    compare_trades,
    ledger_brokers,
    link_warnings,
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
    cb, crv = _lados([(date(2026, 1, 27), D("100.00"), D("105.26"))])

    assert compare(cb, crv)[0].state == DIFFERENCE


def test_exatamente_a_tolerancia_ainda_confere():
    cb, crv = _lados([(date(2026, 1, 27), D("100.00"), D("105.25"))])

    assert compare(cb, crv)[0].state == MATCH
    assert compare(cb, crv, tolerance=D("5.24"))[0].state == DIFFERENCE


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


@pytest.mark.parametrize(("bruto", "esperado"), [(None, D("5.25")), ("", D("5.25")), ("2.50", D("2.50")), ("0", D("0"))])
def test_tolerancia_configuravel_com_padrao_de_cinco_e_vinte_e_cinco(bruto, esperado):
    assert tolerance_from_config({"CONFERENCIA_TOLERANCIA": bruto}) == esperado


@pytest.mark.parametrize(("bruto", "esperado"), [(None, ("Avenue",)), ("", ("Avenue",)), ("Avenue, IBKR", ("Avenue", "IBKR"))])
def test_corretoras_do_exterior_configuraveis_com_padrao_avenue(bruto, esperado):
    assert ledger_brokers({"CONFERENCIA_CORRETORAS_EXTERIOR": bruto}) == esperado


@pytest.mark.parametrize("bruto", ["-1", "abc", "NaN"])
def test_tolerancia_invalida_e_recusada(bruto):
    with pytest.raises(ValueError, match="CONFERENCIA_TOLERANCIA"):
        tolerance_from_config({"CONFERENCIA_TOLERANCIA": bruto})


def test_so_no_crv_vira_aguardando_extrato_quando_a_liquidacao_passa_do_extrato_importado():
    crv = [
        Movement("Genial", date(2026, 9, 10), "BRL", D("100"), "B3_GENIAL_10092026"),
        Movement("Genial", date(2026, 9, 26), "BRL", D("100"), "B3_GENIAL_26092026"),
        Movement("Genial", date(2026, 10, 5), "BRL", D("100"), "B3_GENIAL_05102026"),
        Movement("XP", date(2026, 10, 5), "BRL", D("100"), "B3_XP_05102026"),
    ]

    linhas = compare([], crv, coverage={("Genial", "BRL"): date(2026, 9, 29)})

    assert [(linha.broker, linha.pregao.day, linha.state) for linha in linhas] == [
        ("Genial", 10, ONLY_CRV),  # a liquidação já devia estar no extrato: falta mesmo
        ("Genial", 26, AWAITING_STATEMENT),  # liquida em 29 ou 30/09, na borda do extrato
        ("Genial", 5, AWAITING_STATEMENT),
        ("XP", 5, ONLY_CRV),  # sem cobertura publicada, nada muda
    ]
    assert AWAITING_STATEMENT in STATE_LABELS


def _negocio(dia, ativo, quantidade, valor, corretora="Avenue"):
    return Trade(corretora, dia, "USD", ativo, None if quantidade is None else D(quantidade),
                 None if valor is None else D(valor))


def test_no_exterior_cada_negocio_confere_por_ativo_quantidade_e_valor():
    """Dois negócios no mesmo pregão (CGC e BRK.B) viram duas linhas, uma por ativo."""
    dia = date(2026, 6, 16)
    cb = [_negocio(dia, "CGC", "5000", "-4750.00"), _negocio(dia, "BRK.B", "-1.56365", "780.46")]
    crv = [_negocio(dia, "CGC", "5000", "-4750.00"), _negocio(dia, "BRKB", "-1.56365", "780.4646")]

    linhas = compare_trades(cb, crv)

    assert [(linha.instrument, linha.state) for linha in linhas] == [("BRKB", MATCH), ("CGC", MATCH)]


def test_a_corretagem_de_cinco_dolares_e_o_arredondamento_do_preco_ainda_conferem():
    """Outubro de 2026: o preço do CRV (quatro casas) traz a corretagem de US$ 5 que o CB lança à parte."""
    cb = [_negocio(date(2026, 10, 9), "CGC", "1284", "-1065.72"), _negocio(date(2026, 10, 20), "CGC", "2115", "-1520.90")]
    crv = [_negocio(date(2026, 10, 9), "CGC", "1284", "-1070.7276"), _negocio(date(2026, 10, 20), "CGC", "2115", "-1525.9725")]

    linhas = compare_trades(cb, crv)

    assert [(linha.state, linha.implicit_cost) for linha in linhas] == [(MATCH, D("-5.0076")), (MATCH, D("-5.0725"))]
    assert compare_trades(cb[:1], [_negocio(date(2026, 10, 9), "CGC", "1284", "-1071.00")])[0].state == DIFFERENCE


def test_quantidade_diferente_no_exterior_e_diferenca_mesmo_com_o_valor_igual():
    dia = date(2026, 7, 23)
    linha = compare_trades([_negocio(dia, "CGC", "2500", "-2250.00")], [_negocio(dia, "CGC", "2400", "-2250.00")])[0]

    assert (linha.state, linha.cb_quantity, linha.crv_quantity) == (DIFFERENCE, D("2500"), D("2400"))


def test_sem_preco_no_crv_a_quantidade_basta_e_sem_nada_a_comparar_nao_confere():
    """Posição encerrada antes do arquivo de movimentos: o CRV só sabe a quantidade."""
    dia = date(2026, 2, 11)
    so_quantidade = compare_trades([_negocio(dia, "CGC", "1200", "-1260.00")], [_negocio(dia, "CGC", "1200", None)])[0]
    nada = compare_trades([_negocio(dia, "CGC", None, "-1260.00")], [_negocio(dia, "CGC", "1200", None)])[0]

    assert (so_quantidade.state, so_quantidade.implicit_cost) == (MATCH, None)
    assert nada.state == DIFFERENCE


def test_negocio_so_no_crv_do_exterior_aguarda_o_extrato_quando_passa_da_cobertura():
    linhas = compare_trades(
        [], [_negocio(date(2026, 9, 30), "CGC", "2308", "-2077.20"), _negocio(date(2026, 8, 3), "CGC", "1", "-1")],
        coverage={("Avenue", "USD"): date(2026, 9, 30)},
    )

    assert [(linha.pregao, linha.state) for linha in linhas] == [
        (date(2026, 8, 3), ONLY_CRV), (date(2026, 9, 30), AWAITING_STATEMENT),
    ]


@pytest.mark.parametrize(
    ("extrato", "pregao"),
    [
        (date(2026, 9, 30), date(2026, 9, 29)),  # quarta: o de terça já liquidou
        (date(2026, 1, 31), date(2026, 1, 29)),  # sábado: o de sexta liquida na segunda
        (date(2026, 6, 1), date(2026, 5, 29)),   # segunda: o de sexta liquida hoje
    ],
)
def test_a_carteira_do_extrato_e_a_do_ultimo_pregao_ja_liquidado(extrato, pregao):
    assert settled_cutoff(extrato) == pregao


def test_carteira_do_extrato_contra_a_posicao_do_crv():
    linhas = compare_holdings(
        "Avenue", date(2026, 1, 31), date(2026, 1, 29),
        {"BRKB": D("1.56365"), "CGC": D("1200")},
        {"BRK.B": D("1.56365"), "SGOV": D("0"), "AAPL": D("3")},
    )

    assert [(linha.instrument, linha.statement_quantity, linha.crv_quantity, linha.matches) for linha in linhas] == [
        ("AAPL", D("0"), D("3"), False),
        ("BRK.B", D("1.56365"), D("1.56365"), True),
        ("CGC", D("1200"), D("0"), False),
    ]


INSTITUICOES = {"Genial": ("genial",), "XP": ("scp-xp-investimentos",), "Avenue": ("avenue",)}


def test_vinculo_inteiro_nao_gera_aviso():
    assert link_warnings({"Genial", "Avenue"}, INSTITUICOES, frozenset({"genial", "avenue"}), category_found=True) == []


def test_instituicao_renomeada_no_cb_vira_aviso_e_nao_so_no_crv_calado():
    """A XP mudou de nome no CB: sem o aviso, todo pregão dela viraria "só no CRV"."""
    avisos = link_warnings({"Genial", "XP"}, INSTITUICOES, frozenset({"genial", "xp-investimentos"}), category_found=True)

    assert len(avisos) == 1
    assert "corretora XP" in avisos[0]
    assert "scp-xp-investimentos" in avisos[0]


def test_corretora_sem_negocio_no_recorte_nao_gera_aviso():
    assert link_warnings({"Genial"}, INSTITUICOES, frozenset({"genial"}), category_found=True) == []


def test_corretora_fora_da_correspondencia_e_categoria_sumida_sao_avisadas():
    avisos = link_warnings({"Clear"}, INSTITUICOES, frozenset({"genial"}), category_found=False)

    assert "Operações em Bolsa" in avisos[0]
    assert "corretora Clear" in avisos[1]
    assert "CB_CORRESPONDENCIA_CONTAS" in avisos[1]
