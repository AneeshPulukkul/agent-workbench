"""Alembic env — skeleton (models + migrations: Prompt 4)."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None
try:
    from packages.persistence.models import Base as _Base

    target_metadata = _Base.metadata
except Exception:  # offline / minimal envs
    target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=os.getenv("DATABASE_URL", "sqlite://"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(os.getenv("DATABASE_URL", "sqlite://"))
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
