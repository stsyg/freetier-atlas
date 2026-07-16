"""Database engine and connectivity helpers.

The engine is created lazily so the application (and its unit tests) can import
this module without a live database. Readiness checks use a short-lived
connection and never leak connection strings or credentials into logs.
"""

from __future__ import annotations

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
        connect_args={"connect_timeout": int(settings.readiness_timeout_seconds)},
    )


def check_database() -> None:
    """Execute ``SELECT 1`` against the database.

    Raises the underlying SQLAlchemy error when the database is unreachable so
    callers can translate it into a 503 response.
    """

    engine = get_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
