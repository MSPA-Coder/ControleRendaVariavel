"""A planilha enviada não esgota o worker nem expande entidades XML (SEG-L2).

O limite em bytes (2 MB e 5 MB) vale para o arquivo comprimido. Um XLSX de
poucos KB pode descomprimir em milhões de linhas, e o parser XML padrão expande
entidades. Dois tetos: `defusedxml` instalado (o openpyxl o usa sozinho) e um
máximo de linhas por aba, nos dois importadores (proventos e myProfit).
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.dividends import importer
from app.dividends.importer import DividendWorkbookError, read_dividend_workbook
from app.executions import myprofit
from app.executions.myprofit import ExecutionReportError, load_sheets


def _xlsx(linhas: int) -> bytes:
    livro = Workbook()
    aba = livro.active
    for numero in range(linhas):
        aba.append([f"linha {numero}", numero])
    saida = BytesIO()
    livro.save(saida)
    return saida.getvalue()


def _xlsx_com_entidade() -> bytes:
    """O mesmo XLSX, com uma entidade declarada no XML da planilha."""
    original = zipfile.ZipFile(BytesIO(_xlsx(3)))
    saida = BytesIO()
    with zipfile.ZipFile(saida, "w") as novo:
        for item in original.infolist():
            dados = original.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                declaracao = b'<!DOCTYPE worksheet [<!ENTITY expande "aaaaaaaaaaaaaaaa">]>'
                dados = declaracao + dados
            novo.writestr(item, dados)
    return saida.getvalue()


def test_openpyxl_le_planilhas_com_defusedxml_instalado():
    from openpyxl.xml import DEFUSEDXML

    assert DEFUSEDXML is True


def test_proventos_recusa_planilha_com_entidade_xml():
    with pytest.raises(DividendWorkbookError, match="Não foi possível ler"):
        read_dividend_workbook(_xlsx_com_entidade())


def test_myprofit_recusa_planilha_com_entidade_xml():
    with pytest.raises(ExecutionReportError, match="Não foi possível ler"):
        load_sheets(_xlsx_com_entidade())


def test_proventos_recusa_aba_com_linhas_demais(monkeypatch):
    monkeypatch.setattr(importer, "MAX_SHEET_ROWS", 5)

    with pytest.raises(DividendWorkbookError, match="linhas demais"):
        read_dividend_workbook(_xlsx(10))


def test_myprofit_recusa_aba_com_linhas_demais(monkeypatch):
    monkeypatch.setattr(myprofit, "MAX_SHEET_ROWS", 5)

    with pytest.raises(ExecutionReportError, match="linhas demais"):
        load_sheets(_xlsx(10))


def test_aba_dentro_do_teto_continua_lida(monkeypatch):
    monkeypatch.setattr(myprofit, "MAX_SHEET_ROWS", 10)

    assert len(load_sheets(_xlsx(10))[0]) == 10
