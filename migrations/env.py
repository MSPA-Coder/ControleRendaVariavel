from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config
fileConfig(config.config_file_name)
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
