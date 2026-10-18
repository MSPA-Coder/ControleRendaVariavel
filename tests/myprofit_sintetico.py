"""Relatório 'Histórico de investimentos' SINTÉTICO, no formato do myProfit.

Nenhum valor daqui é dado real do mantenedor: nomes, CPF e números foram
inventados e só reproduzem o formato (duas linhas de cabeçalho, uma de colunas,
data em número serial do Excel, quantidade negativa na venda).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from app.executions.myprofit import REQUIRED_COLUMNS

#: Nome e CPF fictícios: o teste de privacidade procura estes textos em tudo
#: que o importador devolve ou grava.
TITULAR_FICTICIO = "FULANO DE TAL SINTETICO"
CPF_FICTICIO = "123.456.789-09"
CPF_SO_DIGITOS = "12345678909"


def serial(dia: date) -> int:
    """Número serial do Excel para ``dia`` (46028 = 06/01/2026)."""
    return (dia - date(1899, 12, 30)).days


def linha(
    dia: date,
    documento: str,
    ativo: str,
    quantidade: str | int | float,
    total_com_taxas: str | int | float,
    *,
    instituicao: str = "GENIAL",
    grupo: str = "Ações",
    taxas: str | int | float = "1.50",
    tipo: str | None = None,
    operacao: str | None = None,
    obs: str = "Normal",
    moeda: str = "BRL",
    tipo_documento: str = "",
) -> list[object]:
    """Uma linha do relatório. Venda: quantidade negativa e total positivo."""
    qtd = Decimal(str(quantidade))
    total = Decimal(str(total_com_taxas))
    tipo = tipo or ("Venda" if qtd < 0 else "Compra")
    operacao = operacao or ("Crédito" if total >= 0 else "Débito")
    preco = abs(total / qtd) if qtd else Decimal(0)
    return [
        serial(dia),
        instituicao,
        documento,
        tipo_documento,
        moeda,
        float(taxas),
        ativo,
        grupo,
        float(qtd),
        operacao,
        tipo,
        float(preco),
        float(preco),
        float(abs(total)) * (1 if total >= 0 else -1),
        float(total),
        obs,
    ]


def relatorio_xlsx(
    linhas: list[list[object]],
    *,
    periodo: tuple[date, date] = (date(2026, 1, 1), date(2026, 10, 31)),
    colunas: tuple[str, ...] = REQUIRED_COLUMNS,
    cabecalho: bool = True,
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    if cabecalho:
        sheet.append([f"Emitido por: {TITULAR_FICTICIO} - {CPF_FICTICIO}"])
        sheet.append([f"Período: {periodo[0]:%d/%m/%Y} até {periodo[1]:%d/%m/%Y}"])
    sheet.append(list(colunas))
    for item in linhas:
        sheet.append(item)
    saida = BytesIO()
    workbook.save(saida)
    return saida.getvalue()
