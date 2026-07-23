"""Database engine and connectivity helpers.

The engine is created lazily so the application (and its unit tests) can import
this module without a live database. Readiness checks use a short-lived
connection and never leak connection strings or credentials into logs.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

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


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """Return a cached :class:`sessionmaker` bound to the lazy engine.

    Sessions produced here are used by the read-only catalogue API. The engine
    is created lazily (see :func:`get_engine`) so importing this module never
    requires a live database.
    """

    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped, read-only session.

    The catalogue API only ever issues ``SELECT`` statements, so this session is
    never committed; it is always rolled back and closed at the end of the
    request. That keeps the read surface strictly non-mutating even if a future
    handler regression tried to write.
    """

    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def check_database() -> None:
    """Execute ``SELECT 1`` against the database.

    Raises the underlying SQLAlchemy error when the database is unreachable so
    callers can translate it into a 503 response.
    """

    engine = get_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
