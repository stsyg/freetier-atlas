"""Append-only admin audit-log persistence (migration 0009 backing).

Mirrors the S2 abuse-store pattern: a narrow :class:`AdminAuditStore` protocol
with a PostgreSQL backend (writing through autonomous ``engine.begin()``
transactions, independent of the read-only request session) and an in-memory
backend for unit tests.

Security: :func:`_safe_context` strips any secret-looking keys from the audit
``context`` mapping as a defence-in-depth measure, so a token, secret, key,
password, or raw OAuth ``code`` can never reach a persisted audit row even if a
future caller passed one by mistake. The store never receives or stores raw
credentials by design.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.models.admin import AdminAudit

#: Substrings that mark a context key as potentially secret-bearing; any match
#: is dropped before the row is written (defence in depth).
_SECRET_KEY_MARKERS: tuple[str, ...] = (
    "secret",
    "token",
    "password",
    "passwd",
    "authorization",
    "signing_key",
    "client_secret",
    "code",
    "cookie",
)


def _safe_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return ``context`` with any secret-looking keys removed."""

    if not context:
        return None
    cleaned = {
        key: value
        for key, value in context.items()
        if not any(marker in str(key).lower() for marker in _SECRET_KEY_MARKERS)
    }
    return cleaned or None


@dataclass(frozen=True)
class AuditRow:
    """A materialised admin-audit row (used by the in-memory backend / reads)."""

    actor: str | None
    action: str
    outcome: str
    reason: str | None
    context: dict[str, Any] | None
    created_at: datetime


class AdminAuditStore(Protocol):
    """The narrow persistence seam for the admin audit log."""

    def record(
        self,
        *,
        actor: str | None,
        action: str,
        outcome: str,
        reason: str | None,
        context: dict[str, Any] | None,
        now: datetime,
    ) -> None:
        """Append a single audit row."""
        ...


class PostgresAdminAuditStore:
    """PostgreSQL-backed :class:`AdminAuditStore` using autonomous transactions."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(
        self,
        *,
        actor: str | None,
        action: str,
        outcome: str,
        reason: str | None,
        context: dict[str, Any] | None,
        now: datetime,
    ) -> None:
        stmt = sa.insert(AdminAudit.__table__).values(
            actor=actor,
            action=action,
            outcome=outcome,
            reason=reason,
            context=_safe_context(context),
            created_at=now,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)


class InMemoryAdminAuditStore:
    """In-memory :class:`AdminAuditStore` for unit tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rows: list[AuditRow] = []

    def record(
        self,
        *,
        actor: str | None,
        action: str,
        outcome: str,
        reason: str | None,
        context: dict[str, Any] | None,
        now: datetime,
    ) -> None:
        with self._lock:
            self.rows.append(
                AuditRow(
                    actor=actor,
                    action=action,
                    outcome=outcome,
                    reason=reason,
                    context=_safe_context(context),
                    created_at=now,
                )
            )


@lru_cache(maxsize=1)
def get_admin_audit_store() -> AdminAuditStore:
    """Return the process-wide :class:`PostgresAdminAuditStore` (lazily built)."""

    return PostgresAdminAuditStore(get_engine())


__all__ = [
    "AuditRow",
    "AdminAuditStore",
    "PostgresAdminAuditStore",
    "InMemoryAdminAuditStore",
    "get_admin_audit_store",
]
