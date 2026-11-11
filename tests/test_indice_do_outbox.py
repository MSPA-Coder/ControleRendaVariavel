"""O outbox do patrimônio mantém só o índice composto por dono.

`ix_patrimonio_v4_outbox_owner_id` era prefixo de `..._owner_cursor` e foi
removido (revisão 0045). A leitura por dono e cursor, que o contrato v4 faz,
continua por índice.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.banco


def test_outbox_nao_tem_o_indice_redundante_e_a_leitura_por_dono_usa_o_composto(sessao):
    indices = {
        nome for (nome,) in sessao.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = 'patrimonio_v4_outbox'"
        ))
    }

    assert "ix_patrimonio_v4_outbox_owner_id" not in indices
    assert "ix_patrimonio_v4_outbox_owner_cursor" in indices

    sessao.execute(text("SET LOCAL enable_seqscan = off"))
    plano = "\n".join(
        linha for (linha,) in sessao.execute(text(
            "EXPLAIN SELECT cursor FROM patrimonio_v4_outbox "
            "WHERE owner_id = 1 AND cursor > 0 ORDER BY cursor"
        ))
    )
    assert "ix_patrimonio_v4_outbox_owner_cursor" in plano
