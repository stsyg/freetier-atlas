"""Alembic migration environment.

The database URL is read from the ``DATABASE_URL`` environment variable so that
no connection string or credential is stored in the repository. When it is unset
we fall back to the local Docker Compose default, which uses a non-secret local
development credential.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure the API application package (apps/api) is importable when Alembic runs
# from the repository root locally. In the container PYTHONPATH already points at
# /app/apps/api; adding it here makes local ``alembic`` invocations work too.
_APPS_API = Path(__file__).resolve().parents[1] / "apps" / "api"
if _APPS_API.is_dir() and str(_APPS_API) not in sys.path:
    sys.path.insert(0, str(_APPS_API))

from app.models import alembic_include_object  # noqa: E402
from app.models import metadata as domain_metadata  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The domain model metadata (F003 task 004) so autogenerate / compare_metadata
# can detect drift between the ORM models and the migrations. ``include_object``
# scopes comparisons to the domain-owned tables so the infrastructure tables
# from the 0001/0002 migrations are not proposed for removal.
target_metadata = domain_metadata

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
        include_object=alembic_include_object,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=alembic_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
