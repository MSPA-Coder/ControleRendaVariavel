from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config
# `disable_existing_loggers=False` não é detalhe: o padrão do `fileConfig` é
# True, e ele DESLIGA todo logger que já exista no processo -- inclusive o da
# aplicação.
#
# No contêiner isso nunca apareceu, porque o serviço `migrate` roda como
# processo separado, que morre em seguida. Aparece quando a migração é aplicada
# no mesmo processo, como faz a fixture `app_com_banco` da suíte: no MegaSena,
# onde o mesmo padrão existia, isso fez a aplicação parar de registrar um
# WARNING para todos os testes seguintes, e um teste alheio reprovou.
#
# Manter os loggers existentes é a recomendação da própria documentação do
# Alembic para este caso, e não deixa de configurar nada: os loggers declarados
# no `alembic.ini` continuam sendo aplicados.
fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = current_app.extensions["migrate"].db.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(current_app.extensions["migrate"].db.engine.url).replace("%", "%%"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with current_app.extensions["migrate"].db.engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
