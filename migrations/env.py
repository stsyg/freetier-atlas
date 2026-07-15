"""Alembic migration environment.

The database URL is read from the ``DATABASE_URL`` environment variable so that
no connection string or credential is stored in the repository. When it is unset
we fall back to the local Docker Compose default, which uses a non-secret local
development credential.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No ORM models yet (the domain model is F003); autogenerate is not used in this
# slice, so target_metadata stays None.
target_metadata = None

DEFAULT_LOCAL_DATABASE_URL = "postgresql+psycopg://atlas:atlas@postgres:5432/atlas"  # noqa: E501 # pragma: allowlist secret


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_LOCAL_DATABASE_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DB connection)."""

    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database connection."""

    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
