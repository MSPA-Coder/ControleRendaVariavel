"""Retenção do outbox de invalidação do contrato patrimônio v4.

O outbox (`patrimonio_v4_outbox`) recebe uma linha por mutação das tabelas
publicadas, gravada por gatilho na mesma transação. Ninguém o esvaziava: em
10/10/2026 tinha 115 mil linhas em 12 dias.

POR QUE APAGAR NÃO PERDE AVISO. Os dois consumidores, o agendador do
Wealthfolio e o add-on, perguntam ao `/patrimonio/v4/changes` só uma coisa:
existe algum item depois do meu checkpoint (`?after=...&limit=1`)? Qualquer
item leva a um snapshot inteiro, porque o feed é de invalidação
(`snapshot_required`), não de conteúdo. Então basta que a resposta a essa
pergunta não mude. Ela não muda se a linha mais nova que cada dono enxerga
continuar existindo, e o dono enxerga as próprias linhas e as sem dono
(cotações). Por isso o expurgo nunca apaga a linha mais nova de cada
`owner_id`, nem a mais nova com `owner_id` nulo. Um checkpoint anterior a ela
continua vendo um item; um posterior continua não vendo nada.

Não faz `commit`: quem chama é dono do limite transacional.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from app import db
from app.models import PatrimonioV4Outbox

DIAS_DE_RETENCAO = 30


def expurgar_outbox_v4(agora: datetime, dias: int = DIAS_DE_RETENCAO) -> int:
    """Apaga as linhas com mais de `dias` dias, menos a mais nova de cada dono.

    Devolve quantas linhas saíram.
    """
    if dias < 1:
        raise ValueError("A retenção do outbox precisa ser de pelo menos um dia.")
    # `GROUP BY owner_id` junta as linhas sem dono num grupo só, como se quer.
    mais_novas = select(func.max(PatrimonioV4Outbox.cursor)).group_by(
        PatrimonioV4Outbox.owner_id
    )
    resultado = db.session.execute(
        delete(PatrimonioV4Outbox).where(
            PatrimonioV4Outbox.changed_at < agora - timedelta(days=dias),
            PatrimonioV4Outbox.cursor.not_in(mais_novas),
        )
    )
    return int(resultado.rowcount or 0)
