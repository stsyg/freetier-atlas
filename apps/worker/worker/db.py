"""Database engine and connectivity helpers for the worker and scheduler.

The engine is created lazily so unit tests can import this module without a live
database. Connectivity errors never leak connection strings or credentials into
logs; callers surface only the exception type.
"""

from __future__ import annotations

import time
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .settings import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a cached SQLAlchemy engine built from settings."""

    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": int(settings.db_connect_timeout_seconds)},
    )


def check_database(engine: Engine | None = None) -> None:
    """Execute ``SELECT 1``; raise the underlying error when unreachable."""

    engine = engine or get_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def schema_ready(engine: Engine | None = None) -> bool:
    """Return ``True`` when the API-applied ``job_queue`` table exists."""

    engine = engine or get_engine()
    with engine.connect() as connection:
        result = connection.execute(text("SELECT to_regclass('public.job_queue')")).scalar()
    return result is not None


def wait_for_schema(engine: Engine | None = None, timeout: float | None = None) -> bool:
    """Block until the migration-created schema is present or ``timeout`` elapses.

    The API service applies Alembic migrations on startup. The worker and
    scheduler only depend on PostgreSQL being healthy, so this bounded wait
    avoids logging missing-relation errors during the brief startup race.
    Returns ``True`` if the schema became ready, ``False`` on timeout.
    """

    engine = engine or get_engine()
    deadline = time.monotonic() + (
        timeout if timeout is not None else get_settings().schema_wait_timeout_seconds
    )
    while time.monotonic() < deadline:
        try:
            if schema_ready(engine):
                return True
        except Exception:  # noqa: BLE001 - database not up yet; retry until deadline
            pass
        time.sleep(1.0)
    return False
