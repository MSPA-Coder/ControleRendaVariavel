"""A escrita financeira feita fora de requisição entra na trilha com origem `cli` (SEG-L4).

285 linhas de produção tinham `ip` nulo e nada que dissesse se vieram de uma
rotina, da CLI ou de um shell. O registro automático (evento do ORM) passa a
marcar `details.origem = "cli"` quando não há requisição.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import AuditLog, Broker, Market, Portfolio, Position, Side, Ticker, User

pytestmark = pytest.mark.banco


def test_escrita_de_posicao_fora_de_requisicao_grava_origem_cli(sessao):
    sufixo = uuid4().hex[:8]
    usuario = User(username=f"cli-{sufixo}", password_hash="!")
    corretora = Broker(name=f"Corretora {sufixo}", acronym=sufixo[:6])
    papel = Ticker(
        symbol=f"C{sufixo}", trading_name="Papel", market=Market.B3,
        rtd_market_code="B", currency="BRL",
    )
    sessao.add_all([usuario, corretora, papel])
    sessao.flush()
    carteira = Portfolio(owner_id=usuario.id, name=f"Carteira {sufixo}", currency="BRL")
    sessao.add(carteira)
    sessao.flush()
    posicao = Position(
        owner_id=usuario.id, broker_id=corretora.id, ticker_id=papel.id, portfolio_id=carteira.id,
        quantity=Decimal("10"), average_cost=Decimal("5"), side=Side.BUY, opened_on=date(2026, 6, 1),
    )
    sessao.add(posicao)
    sessao.flush()

    linha = sessao.scalars(
        select(AuditLog).where(AuditLog.entity == "Position", AuditLog.entity_id == str(posicao.id))
    ).one()

    assert linha.action == "criar"
    assert linha.ip is None
    assert linha.details["origem"] == "cli"
    assert linha.details["ticker_id"] == str(papel.id)
