"""Conciliação mensal sem banco: leitura da posição (XLSX e TSV), comparação e notas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.executions.conciliacao import (
    ACKNOWLEDGED,
    AVERAGE,
    MATCH,
    ONLY_CRV,
    ONLY_EXECUTIONS,
    ONLY_MYPROFIT,
    QUANTITY,
    Holding,
    apply_notes,
    combine,
    compare_positions,
    compare_sales,
)
from app.executions.myprofit import ExecutionReportError, read_execution_report
from app.executions.posicao_myprofit import read_position_report
from tests.myprofit_posicao_sintetico import linha_posicao, posicao_tsv, posicao_xlsx
from tests.myprofit_sintetico import (
    CPF_FICTICIO,
    CPF_SO_DIGITOS,
    TITULAR_FICTICIO,
    linha,
    relatorio_xlsx,
)

D = Decimal


def _h(quantidade: str, medio: str | None = None) -> Holding:
    return Holding(D(quantidade), D(medio) if medio is not None else None)


@pytest.mark.parametrize("formato", [posicao_xlsx, posicao_tsv], ids=["xlsx", "tsv"])
def test_posicao_le_a_data_final_e_so_as_colunas_que_importam(formato):
    relatorio = read_position_report(
        formato(
            [
                linha_posicao("ABCD3", 100, "10.5"),
                linha_posicao("OPCA20", 2000, "0.09", grupo="Opções"),
                linha_posicao("Tesouro IPCA+ 2035", "4.32", "2885.9277", grupo="Tesouro Direto"),
                linha_posicao("CGC", 500, "4.95", grupo="Ações EUA"),
            ]
        )
    )

    assert relatorio.as_of == date(2026, 10, 31)
    assert relatorio.rejected_rows == []
    assert [(r.asset, r.in_scope) for r in relatorio.rows] == [
        ("ABCD3", True),
        ("OPCA20", True),
        ("Tesouro IPCA+ 2035", False),
        ("CGC", False),
    ]
    acao = relatorio.rows[0]
    assert (acao.quantity, acao.average_price, acao.asset_group) == (D("100"), D("10.5"), "acao")


def test_posicao_aceita_a_data_pela_linha_periodo_e_recusa_data_ilegivel():
    tsv = posicao_tsv([linha_posicao("ABCD3", 1, "1")]).decode()
    com_periodo = tsv.replace("Posição em: 31/10/2026", "Período: 01/10/2026 até 30/10/2026")
    sem_data = tsv.replace("Posição em: 31/10/2026", "Posição em: ontem")

    assert read_position_report(com_periodo.encode()).as_of == date(2026, 10, 30)
    with pytest.raises(ExecutionReportError, match="Emitido por"):
        read_position_report(sem_data.encode())


def test_posicao_nunca_expoe_o_titular():
    relatorio = read_position_report(posicao_xlsx([linha_posicao("ABCD3", 1, "1")]))

    visivel = repr(relatorio)
    assert TITULAR_FICTICIO not in visivel
    assert CPF_FICTICIO not in visivel and CPF_SO_DIGITOS not in visivel


def test_posicao_recusa_linha_com_quantidade_negativa_e_segue_com_as_outras():
    relatorio = read_position_report(
        posicao_tsv([linha_posicao("ABCD3", -5, "10"), linha_posicao("WXYZ3", 5, "10")])
    )

    assert [r.asset for r in relatorio.rows] == ["WXYZ3"]
    assert len(relatorio.rejected_rows) == 1 and "negativos" in relatorio.rejected_rows[0]


def test_posicao_recusa_arquivo_de_outro_relatorio():
    outro = relatorio_xlsx([linha(date(2026, 1, 6), "B3_X_06012026", "ABCD3", -1, "10")])

    with pytest.raises(ExecutionReportError, match="Qtd"):
        read_position_report(outro)


def test_posicao_recusa_arquivo_sem_as_colunas():
    sem_qtd = posicao_tsv([linha_posicao("ABCD3", 1, "1")]).replace(b"Qtd", b"Quantidade", 1)

    with pytest.raises(ExecutionReportError, match="Qtd"):
        read_position_report(sem_qtd)


def test_importador_de_execucoes_tambem_le_o_tsv_do_navegador():
    cabecalho = "\t".join(
        [
            "Data de negociação", "Instituição", "Número", "Documento", "Moeda", "Total de Taxas",
            "Ativo", "Grupo", "Quantidade", "Operação", "Tipo", "Preço sem taxas",
            "Preço com taxas", "Total sem taxas", "Total com taxas", "Obs",
        ]
    )
    dados = "\t".join(
        [
            "46049", "GENIAL", "B3_GENIAL_27012026", "", "BRL", "1.5", "ABCD3", "Ações", "-100",
            "Crédito", "Venda", "10", "10", "1000", "1000", "Normal",
        ]
    )
    tsv = (
        "Emitido por: TITULAR ANONIMIZADO - 000.000.000-00\n"
        "Período: 01/01/2026 até 31/10/2026\n"
        f"{cabecalho}\n{dados}\n"
    ).encode()

    lido = read_execution_report(tsv)

    assert lido.rejected_rows == []
    assert [(r.trade_date, r.asset, r.quantity, r.total_with_fees) for r in lido.rows] == [
        (date(2026, 1, 27), "ABCD3", D("-100"), D("1000"))
    ]


def test_combine_soma_lotes_e_pondera_o_custo_medio():
    assert combine([_h("10", "10"), _h("30", "20")]) == Holding(D("40"), D("17.5"))
    assert combine([]) is None


def test_posicao_confere_dentro_da_tolerancia_do_custo_medio():
    # O myProfit guarda quatro casas; o CRV, oito.
    achados = compare_positions({"RAIZ4": _h("254000", "0.4626")}, {"RAIZ4": _h("254000", "0.4626437")})

    assert [a.state for a in achados] == [MATCH]


@pytest.mark.parametrize(
    ("myprofit", "crv", "estado"),
    [
        (_h("100", "10"), _h("90", "10"), QUANTITY),
        (_h("100", "10"), _h("100", "10.5"), AVERAGE),
        (_h("100", "10"), None, ONLY_MYPROFIT),
        (None, _h("100", "10"), ONLY_CRV),
    ],
)
def test_posicao_classifica_cada_diferenca(myprofit, crv, estado):
    esquerda = {"ABCD3": myprofit} if myprofit else {}
    direita = {"ABCD3": crv} if crv else {}

    (achado,) = compare_positions(esquerda, direita)

    assert achado.state == estado and achado.pending is True


def test_venda_so_nas_execucoes_acusa_o_ciclo_que_ficou_de_fora():
    achados = {
        a.asset: a
        for a in compare_sales(
            {"WEGE3": D("600"), "HODL11": D("667"), "OPCX1": D("10")},
            {"HODL11": D("667"), "SO3": D("5"), "OPCX1": D("8")},
        )
    }

    assert achados["WEGE3"].state == ONLY_EXECUTIONS
    assert achados["HODL11"].state == MATCH
    assert achados["SO3"].state == ONLY_CRV
    assert achados["OPCX1"].state == QUANTITY


def test_nota_reconhece_a_pendencia_mas_nao_esconde_o_que_confere():
    achados = compare_sales({"RAIZO700": D("35000"), "HODL11": D("667")}, {"HODL11": D("667")})
    notas = {
        ("venda", "RAIZO700"): "atribuição: prêmio no custo da ação",
        ("venda", "HODL11"): "irrelevante",
    }

    final = {a.asset: a for a in apply_notes(achados, notas)}

    assert final["RAIZO700"].state == ACKNOWLEDGED and final["RAIZO700"].pending is False
    assert final["RAIZO700"].note == "atribuição: prêmio no custo da ação"
    assert final["HODL11"].state == MATCH and final["HODL11"].note is None


def test_nota_de_um_tipo_nao_vale_para_o_outro():
    achados = compare_positions({"ABCD3": _h("10", "1")}, {})

    (final,) = apply_notes(achados, {("venda", "ABCD3"): "outra coisa"})

    assert final.state == ONLY_MYPROFIT
