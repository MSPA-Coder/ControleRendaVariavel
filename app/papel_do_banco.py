"""Recusa atender como superusuário do PostgreSQL.

A aplicação conecta com o papel próprio `investimentos_app`, só com DML (ver
`scripts/provision-db-runtime.sh`); o superusuário `investimentos` fica no
banco e nos serviços `migrate` e `db-provision`. O `compose.yaml` já separa os
dois, mas nada no código impedia que um `.env` ou um compose alterado voltasse
a apontar o `web` para o superusuário -- o nome não diz nada, o atributo diz.
Por isso a pergunta vai ao próprio servidor, em cada conexão nova do pool.

Só vale onde `DB_EXIGIR_PAPEL_RESTRITO=1`: o `migrate` e a suíte usam o papel
administrativo de propósito. Cópia da trava do MegaSena (`app/papel_do_banco.py`
de lá); mudança numa vale para as outras.
"""

from __future__ import annotations

import os

from sqlalchemy import event

VARIAVEL = "DB_EXIGIR_PAPEL_RESTRITO"


class PapelPrivilegiadoError(RuntimeError):
    """A conexão foi aberta com um papel superusuário."""


def exigido() -> bool:
    return os.environ.get(VARIAVEL, "").strip() == "1"


def conferir_conexao(dbapi_connection, _registro=None) -> None:
    """Receptor do evento `connect`: superusuário derruba a conexão."""
    if not exigido():
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SHOW is_superuser")
        superusuario = cursor.fetchone()[0] == "on"
    finally:
        cursor.close()
    # O SHOW abre transação implícita no psycopg; devolvê-la limpa ao pool.
    if not getattr(dbapi_connection, "autocommit", False):
        dbapi_connection.rollback()
    if superusuario:
        raise PapelPrivilegiadoError(
            "O papel do banco é superusuário do PostgreSQL. A aplicação deve "
            "conectar com o papel restrito provisionado por "
            "scripts/provision-db-runtime.sh."
        )


def instalar(engine) -> None:
    if not event.contains(engine, "connect", conferir_conexao):
        event.listen(engine, "connect", conferir_conexao)
