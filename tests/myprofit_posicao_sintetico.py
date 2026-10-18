"""Relatório 'Posição detalhada' SINTÉTICO, no formato do myProfit.

Nenhum valor daqui é dado real do mantenedor: nome, CPF e números foram
inventados e só reproduzem o formato (duas linhas de cabeçalho, uma de colunas,
posição somada por ativo, sem corretora).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from tests.myprofit_sintetico import CPF_FICTICIO, TITULAR_FICTICIO

COLUNAS_POSICAO = (
    "Ativo", "CNPJ", "Qtd", "Preço medio", "Total investido", "Preço atual", "Total atual",
    "Tipo", "Setor", "Grupo", "Categoria", "Emissor", "Indexador", "Taxa", "Vencimento",
    "% Patrimônio", "% Ganho", "Ganho",
)


def linha_posicao(
    ativo: str, quantidade: str | int | float, preco_medio: str | int | float, *, grupo: str = "Ações"
) -> list[object]:
    """Uma linha da 'Posição detalhada'."""
    qtd = Decimal(str(quantidade))
    medio = Decimal(str(preco_medio))
    return [
        ativo, "00.000.000/0001-00", float(qtd), float(medio), float(qtd * medio), 1.0,
        float(qtd), "Bovespa", "Setor", grupo, "Hold", " ", " ", " ", " ", 1.0, 0.0, 0.0,
    ]


def posicao_xlsx(linhas: list[list[object]], *, ate: date = date(2026, 10, 31)) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([f"Emitido por: {TITULAR_FICTICIO} - {CPF_FICTICIO}"])
    sheet.append([f"Período: {ate.replace(day=1):%d/%m/%Y} até {ate:%d/%m/%Y}"])
    sheet.append(list(COLUNAS_POSICAO))
    for item in linhas:
        sheet.append(item)
    saida = BytesIO()
    workbook.save(saida)
    return saida.getvalue()


def posicao_tsv(linhas: list[list[object]], *, ate: date = date(2026, 10, 31)) -> bytes:
    """O mesmo relatório como o navegador o extrai: TSV, titular já anonimizado."""
    saida = ["Emitido por: TITULAR ANONIMIZADO - 000.000.000-00"]
    saida.append(f"Período: {ate.replace(day=1):%d/%m/%Y} até {ate:%d/%m/%Y}")
    saida.append("\t".join(COLUNAS_POSICAO))
    for item in linhas:
        saida.append("\t".join("" if c in (None, " ") else str(c) for c in item))
    return "\n".join(saida).encode("utf-8")
