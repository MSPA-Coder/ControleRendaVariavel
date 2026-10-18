"""O leitor do relatório 'Histórico de investimentos' do myProfit (sem banco)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.executions.myprofit import (
    MAX_WORKBOOK_BYTES,
    REQUIRED_COLUMNS,
    ExecutionReportError,
    is_in_scope,
    read_execution_report,
    safe_file_name,
)
from tests.myprofit_sintetico import (
    CPF_FICTICIO,
    CPF_SO_DIGITOS,
    TITULAR_FICTICIO,
    linha,
    relatorio_xlsx,
    serial,
)

DIA = date(2026, 1, 6)


def test_serial_do_excel_vira_a_data_do_pregao():
    assert serial(date(2026, 1, 6)) == 46028
    relatorio = read_execution_report(
        relatorio_xlsx([linha(DIA, "B3_GENIAL_06012026", "ABCD3", -100, "1000.00")])
    )

    assert relatorio.rows[0].trade_date == date(2026, 1, 6)
    assert (relatorio.period_start, relatorio.period_end) == (date(2026, 1, 1), date(2026, 10, 31))


def test_leitura_das_colunas_da_venda_e_da_compra():
    relatorio = read_execution_report(
        relatorio_xlsx(
            [
                linha(DIA, "B3_GENIAL_06012026", "ABCD3", -100, "1000.25", taxas="0.75"),
                linha(DIA, "B3_GENIAL_06012026", "WXYZ11", 40, "-480.10", grupo="ETF"),
            ]
        )
    )

    venda, compra = relatorio.rows
    assert relatorio.rejected_rows == []
    assert (venda.asset, venda.asset_group, venda.side, venda.cash_operation) == ("ABCD3", "acao", "V", "C")
    assert venda.quantity == Decimal("-100")
    assert venda.total_with_fees == Decimal("1000.25")
    assert venda.fees_total == Decimal("0.75")
    assert (compra.asset_group, compra.side, compra.cash_operation) == ("etf", "C", "D")
    assert compra.total_with_fees == Decimal("-480.10")
    assert isinstance(compra.total_with_fees, Decimal)
    assert venda.in_scope and compra.in_scope


def test_linhas_iguais_no_mesmo_documento_sao_negocios_distintos():
    """Quatro vendas idênticas num pregão são legítimas: nenhuma pode se perder."""
    iguais = linha(DIA, "B3_GENIAL_06012026", "ABCD3", -10, "100.00")
    relatorio = read_execution_report(relatorio_xlsx([iguais, iguais, iguais]))

    assert [row.line_order for row in relatorio.rows] == [1, 2, 3]
    assert len({row.content_hash for row in relatorio.rows}) == 1


def test_a_ordem_conta_por_documento_e_continua_apos_linha_recusada():
    ruim = linha(DIA, "DOC_A", "ABCD3", -10, "100.00", grupo="Grupo Inventado")
    relatorio = read_execution_report(
        relatorio_xlsx(
            [
                linha(DIA, "DOC_A", "ABCD3", -10, "100.00"),
                ruim,
                linha(DIA, "DOC_A", "ABCD3", -20, "200.00"),
                linha(DIA, "DOC_B", "ABCD3", -30, "300.00"),
            ]
        )
    )

    assert [(row.document, row.line_order) for row in relatorio.rows] == [
        ("DOC_A", 1),
        ("DOC_A", 3),
        ("DOC_B", 1),
    ]
    assert len(relatorio.rejected_rows) == 1
    assert "grupo" in relatorio.rejected_rows[0]


def test_day_trade_de_futuro_mantem_tipo_e_operacao_e_o_sinal_do_total_e_a_verdade():
    """Tipo 'Venda' com Operação 'Débito' existe no relatório real; nada é 'corrigido'."""
    relatorio = read_execution_report(
        relatorio_xlsx(
            [
                linha(
                    DIA, "B3_BMF_GENIAL_06012026", "WINM26", -1, "-581.44",
                    grupo="Índice Futuro", obs="DayTrade", tipo="Venda", operacao="Débito",
                )
            ]
        )
    )

    futuro = relatorio.rows[0]
    assert (futuro.side, futuro.cash_operation) == ("V", "D")
    assert futuro.total_with_fees == Decimal("-581.44")
    assert futuro.day_trade is True
    assert futuro.asset_group == "indice_futuro"
    assert futuro.in_scope is False


@pytest.mark.parametrize(
    ("grupo", "documento", "em_escopo"),
    [
        ("Ações", "B3_GENIAL_06012026", True),
        ("Opções", "B3_GENIAL_06012026", True),
        ("ETF", "B3_GENIAL_06012026", True),
        ("Índice Futuro", "B3_BMF_GENIAL_06012026", False),
        ("Tesouro Direto", "B3_TD_XP_23062026", False),
        ("Ações EUA", "Avenue_11022026", False),
        ("ETF USA", "20260811", False),
        ("Ações", "B3_ALUGUEL_GENIAL_26062026", False),
    ],
)
def test_so_acoes_opcoes_e_etf_da_b3_ficam_em_escopo(grupo, documento, em_escopo):
    relatorio = read_execution_report(
        relatorio_xlsx([linha(DIA, documento, "ABCD3", 1, "-10.00", grupo=grupo)])
    )

    assert relatorio.rejected_rows == []
    assert relatorio.rows[0].in_scope is em_escopo
    assert is_in_scope(relatorio.rows[0].asset_group, documento) is em_escopo


def test_quantidade_fracionada_e_hash_estavel_na_releitura():
    conteudo = relatorio_xlsx([linha(DIA, "Avenue_X", "BRKB", "-1.56365", "780.47", grupo="Ações EUA", moeda="USD")])

    primeira = read_execution_report(conteudo).rows[0]
    segunda = read_execution_report(conteudo).rows[0]

    assert primeira.quantity == Decimal("-1.56365")
    assert primeira.currency == "USD"
    assert primeira.content_hash == segunda.content_hash


def test_o_hash_muda_quando_um_valor_muda():
    base = read_execution_report(relatorio_xlsx([linha(DIA, "D", "ABCD3", -10, "100.00")])).rows[0]
    outro = read_execution_report(relatorio_xlsx([linha(DIA, "D", "ABCD3", -10, "100.01")])).rows[0]

    assert base.content_hash != outro.content_hash


@pytest.mark.parametrize("faltando", ["Total com taxas", "Número", "Obs"])
def test_arquivo_com_coluna_faltando_e_recusado_com_o_nome_da_coluna(faltando):
    colunas = tuple(coluna for coluna in REQUIRED_COLUMNS if coluna != faltando)

    with pytest.raises(ExecutionReportError, match=faltando):
        read_execution_report(relatorio_xlsx([], colunas=colunas))


def test_arquivo_sem_as_linhas_de_cabecalho_do_relatorio_e_recusado():
    with pytest.raises(ExecutionReportError, match="Emitido por"):
        read_execution_report(relatorio_xlsx([], cabecalho=False))


def test_arquivo_vazio_grande_ou_ilegivel_e_recusado():
    with pytest.raises(ExecutionReportError):
        read_execution_report(b"")
    with pytest.raises(ExecutionReportError, match="2 MB"):
        read_execution_report(b"x" * (MAX_WORKBOOK_BYTES + 1))
    with pytest.raises(ExecutionReportError, match="ler"):
        read_execution_report(b"isto nao e um xlsx")


@pytest.mark.parametrize(
    ("campo", "valor", "trecho"),
    [
        (8, 0, "quantidade"),
        (5, -1, "negativos"),
        (9, "Transferência", "operação"),
        (10, "Troca", "tipo"),
        (14, "NaN", "total com taxas"),
        (14, "", "total com taxas"),
        (0, "ontem", "data"),
        (4, "REAL", "moeda"),
    ],
)
def test_linha_invalida_e_recusada_sem_derrubar_as_demais(campo, valor, trecho):
    ruim = linha(DIA, "D", "ABCD3", -10, "100.00")
    ruim[campo] = valor
    relatorio = read_execution_report(
        relatorio_xlsx([ruim, linha(DIA, "D", "ABCD3", -20, "200.00")])
    )

    assert len(relatorio.rows) == 1
    assert len(relatorio.rejected_rows) == 1
    assert trecho in relatorio.rejected_rows[0].casefold()


def test_nome_e_cpf_do_cabecalho_nao_aparecem_em_nada_que_o_leitor_devolve():
    """O cabeçalho 'Emitido por' é descartado: nem o resultado nem os erros o carregam."""
    conteudo = relatorio_xlsx(
        [
            linha(DIA, "D", "ABCD3", -10, "100.00"),
            linha(DIA, "D", "ABCD3", -10, "100.00", grupo="Inventado"),
        ]
    )

    relatorio = read_execution_report(conteudo)

    visivel = repr(relatorio) + " ".join(relatorio.rejected_rows)
    for segredo in (TITULAR_FICTICIO, "FULANO", CPF_FICTICIO, CPF_SO_DIGITOS):
        assert segredo not in visivel


def test_erro_de_cabecalho_nao_repete_o_conteudo_das_duas_primeiras_linhas():
    with pytest.raises(ExecutionReportError) as erro:
        read_execution_report(relatorio_xlsx([], colunas=("so uma coluna",)))

    for segredo in (TITULAR_FICTICIO, CPF_FICTICIO):
        assert segredo not in str(erro.value)


def test_periodo_invertido_e_recusado():
    with pytest.raises(ExecutionReportError, match="período"):
        read_execution_report(relatorio_xlsx([], periodo=(date(2026, 10, 31), date(2026, 1, 1))))


def test_nome_do_arquivo_gravado_perde_sequencia_de_cpf_e_caminho():
    assert safe_file_name("C:\\Users\\x\\Historico 123.456.789-09.xlsx") == "Historico .xlsx"
    assert safe_file_name("relatorio-12345678909.xlsx") == "relatorio-.xlsx"
    assert safe_file_name("historico.xlsx") == "historico.xlsx"
    assert safe_file_name(None) is None
