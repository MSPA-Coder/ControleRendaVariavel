"""Lançamentos de renda sintéticos, no formato do contrato `patrimonio/v4` (sem dado real)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.patrimonio.cb_client import CbIncomeEntry

D = Decimal
GENIAL = "conta-g"
XP = "conta-x"
INSTITUICOES = {"genial": "Genial", "scp-xp-investimentos": "XP"}
TICKERS = {"RAIZ4", "SMTO3", "EALT4", "BBAS3", "WEGE3"}
CORTE = date(2026, 1, 1)


def lancamento(
    entry_id,
    role,
    valor,
    dia,
    *,
    ativo=None,
    kind="aluguel",
    conta=GENIAL,
    instituicao="genial",
    moeda="BRL",
    fluxo=None,
):
    fluxo = fluxo or ("receita" if role in {"credito", "estorno"} else "despesa")
    return CbIncomeEntry(
        entry_id=str(entry_id),
        account_id=conta,
        institution=instituicao,
        account_name="Conta sintética",
        currency=moeda,
        realized_date=dia,
        amount=D(valor),
        flow=fluxo,
        instrument=ativo,
        kind=kind,
        role=role,
        description=f"sintético {role}",
    )
