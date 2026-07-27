"""Persistence for the abuse layer: an :class:`AbuseStore` seam + two backends.

The :class:`AbuseStore` protocol is the narrow persistence seam used by the
service/breaker/pow helpers. Two implementations exist:

* :class:`PostgresAbuseStore` -- the production backend. It writes through the
  shared engine using short, self-committing ``engine.begin()`` transactions so
  it is completely independent of the read-only request session
  (:func:`app.db.get_session`, which never commits). Counters use PostgreSQL
  ``INSERT ... ON CONFLICT DO UPDATE`` upserts; dedupe and proof-of-work consume
  use ``SELECT ... FOR UPDATE`` so concurrent duplicates collapse correctly.
* :class:`InMemoryAbuseStore` -- a dependency-free backend with identical
  semantics, used by unit tests (which have no database).

No raw IP or request body is ever stored -- only keyed HMAC digests supplied by
the caller (see :mod:`app.adviser.abuse.hashing`).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.models.abuse import (
    AbuseFlag,
    CircuitBreaker,
    PowChallenge,
    RateLimitBucket,
    RequestDedupe,
)


@dataclass(frozen=True)
class DedupeResult:
    """Outcome of recording a request against the dedupe table."""

    is_duplicate: bool
    hit_count: int


@dataclass(frozen=True)
class BreakerRecord:
    """Persisted per-provider circuit-breaker state."""

    state: str
    consecutive_failures: int
    opened_at: datetime | None


class AbuseStore(Protocol):
    """The narrow persistence seam used by the abuse layer."""

    def incr_rate(self, ip_hash: str, scope: str, window_key: int, now: datetime) -> int:
        """Increment and return the per-IP counter for ``(scope, window_key)``."""
        ...

    def get_flag(self, name: str) -> bool:
        """Return the persisted boolean flag ``name`` (missing -> ``False``)."""
        ...

    def set_flag(self, name: str, enabled: bool, now: datetime) -> None:
        """Upsert the persisted boolean flag ``name``."""
        ...

    def record_dedupe(
        self, dedupe_key: str, scope: str, now: datetime, window_seconds: int
    ) -> DedupeResult:
        """Record a request; report whether it duplicates one inside the window."""
        ...

    def breaker_load(self, provider: str) -> BreakerRecord | None:
        """Return the persisted breaker record for ``provider`` (or ``None``)."""
        ...

    def breaker_store(self, provider: str, record: BreakerRecord, now: datetime) -> None:
        """Upsert the breaker record for ``provider``."""
        ...

    def pow_issue(
        self,
        challenge_id: str,
        difficulty: int,
        ip_hash: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> None:
        """Persist a freshly issued proof-of-work challenge."""
        ...

    def pow_consume(self, challenge_id: str, ip_hash: str, now: datetime) -> bool:
        """Atomically mark a challenge solved; ``False`` if unusable/expired."""
        ...

    def pow_purge_expired(self, now: datetime) -> None:
        """Best-effort removal of expired, unsolved challenges."""
        ...


class PostgresAbuseStore:
    """PostgreSQL-backed :class:`AbuseStore` using autonomous transactions."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def incr_rate(self, ip_hash: str, scope: str, window_key: int, now: datetime) -> int:
        table = RateLimitBucket.__table__
        stmt = (
            pg_insert(table)
            .values(
                ip_hash=ip_hash,
                scope=scope,
                window_key=window_key,
                request_count=1,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[table.c.ip_hash, table.c.scope, table.c.window_key],
                set_={"request_count": table.c.request_count + 1, "updated_at": now},
            )
            .returning(table.c.request_count)
        )
        with self._engine.begin() as conn:
            return int(conn.execute(stmt).scalar_one())

    def get_flag(self, name: str) -> bool:
        table = AbuseFlag.__table__
        stmt = sa.select(table.c.enabled).where(table.c.name == name)
        with self._engine.begin() as conn:
            value = conn.execute(stmt).scalar_one_or_none()
        return bool(value) if value is not None else False

    def set_flag(self, name: str, enabled: bool, now: datetime) -> None:
        table = AbuseFlag.__table__
        stmt = (
            pg_insert(table)
            .values(name=name, enabled=enabled, updated_at=now)
            .on_conflict_do_update(
                index_elements=[table.c.name],
                set_={"enabled": enabled, "updated_at": now},
            )
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def record_dedupe(
        self, dedupe_key: str, scope: str, now: datetime, window_seconds: int
    ) -> DedupeResult:
        table = RequestDedupe.__table__
        with self._engine.begin() as conn:
            row = conn.execute(
                sa.select(table.c.last_seen, table.c.hit_count)
                .where(table.c.dedupe_key == dedupe_key)
                .with_for_update()
            ).first()
            if row is None:
                conn.execute(
                    sa.insert(table).values(
                        dedupe_key=dedupe_key,
                        scope=scope,
                        first_seen=now,
                        last_seen=now,
                        hit_count=1,
                    )
                )
                return DedupeResult(is_duplicate=False, hit_count=1)
            last_seen, hit_count = row
            if (now - last_seen).total_seconds() <= window_seconds:
                new_hits = int(hit_count) + 1
                conn.execute(
                    sa.update(table)
                    .where(table.c.dedupe_key == dedupe_key)
                    .values(last_seen=now, hit_count=new_hits)
                )
                return DedupeResult(is_duplicate=True, hit_count=new_hits)
            conn.execute(
                sa.update(table)
                .where(table.c.dedupe_key == dedupe_key)
                .values(first_seen=now, last_seen=now, hit_count=1)
            )
            return DedupeResult(is_duplicate=False, hit_count=1)

    def breaker_load(self, provider: str) -> BreakerRecord | None:
        table = CircuitBreaker.__table__
        stmt = sa.select(table.c.state, table.c.consecutive_failures, table.c.opened_at).where(
            table.c.provider == provider
        )
        with self._engine.begin() as conn:
            row = conn.execute(stmt).first()
        if row is None:
            return None
        return BreakerRecord(
            state=row.state,
            consecutive_failures=int(row.consecutive_failures),
            opened_at=row.opened_at,
        )

    def breaker_store(self, provider: str, record: BreakerRecord, now: datetime) -> None:
        table = CircuitBreaker.__table__
        stmt = (
            pg_insert(table)
            .values(
                provider=provider,
                state=record.state,
                consecutive_failures=record.consecutive_failures,
                opened_at=record.opened_at,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[table.c.provider],
                set_={
                    "state": record.state,
                    "consecutive_failures": record.consecutive_failures,
                    "opened_at": record.opened_at,
                    "updated_at": now,
                },
            )
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def pow_issue(
        self,
        challenge_id: str,
        difficulty: int,
        ip_hash: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> None:
        table = PowChallenge.__table__
        stmt = sa.insert(table).values(
            challenge_id=challenge_id,
            difficulty=difficulty,
            ip_hash=ip_hash,
            issued_at=issued_at,
            expires_at=expires_at,
            solved_at=None,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def pow_consume(self, challenge_id: str, ip_hash: str, now: datetime) -> bool:
        table = PowChallenge.__table__
        with self._engine.begin() as conn:
            row = conn.execute(
                sa.select(table.c.expires_at, table.c.solved_at, table.c.ip_hash)
                .where(table.c.challenge_id == challenge_id)
                .with_for_update()
            ).first()
            if row is None or row.solved_at is not None:
                return False
            if now > row.expires_at or row.ip_hash != ip_hash:
                return False
            conn.execute(
                sa.update(table).where(table.c.challenge_id == challenge_id).values(solved_at=now)
            )
            return True

    def pow_purge_expired(self, now: datetime) -> None:
        table = PowChallenge.__table__
        stmt = sa.delete(table).where(table.c.expires_at < now, table.c.solved_at.is_(None))
        with self._engine.begin() as conn:
            conn.execute(stmt)


class InMemoryAbuseStore:
    """In-memory :class:`AbuseStore` with the same semantics, for tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rate: dict[tuple[str, str, int], int] = {}
        self._flags: dict[str, bool] = {}
        self._dedupe: dict[str, tuple[str, datetime, datetime, int]] = {}
        self._breakers: dict[str, BreakerRecord] = {}
        self._pow: dict[str, tuple[int, str, datetime, datetime, datetime | None]] = {}

    def incr_rate(self, ip_hash: str, scope: str, window_key: int, now: datetime) -> int:
        with self._lock:
            key = (ip_hash, scope, window_key)
            self._rate[key] = self._rate.get(key, 0) + 1
            return self._rate[key]

    def get_flag(self, name: str) -> bool:
        with self._lock:
            return self._flags.get(name, False)

    def set_flag(self, name: str, enabled: bool, now: datetime) -> None:
        with self._lock:
            self._flags[name] = enabled

    def record_dedupe(
        self, dedupe_key: str, scope: str, now: datetime, window_seconds: int
    ) -> DedupeResult:
        with self._lock:
            existing = self._dedupe.get(dedupe_key)
            if existing is None:
                self._dedupe[dedupe_key] = (scope, now, now, 1)
                return DedupeResult(is_duplicate=False, hit_count=1)
            _scope, first_seen, last_seen, hit_count = existing
            if (now - last_seen).total_seconds() <= window_seconds:
                new_hits = hit_count + 1
                self._dedupe[dedupe_key] = (scope, first_seen, now, new_hits)
                return DedupeResult(is_duplicate=True, hit_count=new_hits)
            self._dedupe[dedupe_key] = (scope, now, now, 1)
            return DedupeResult(is_duplicate=False, hit_count=1)

    def breaker_load(self, provider: str) -> BreakerRecord | None:
        with self._lock:
            return self._breakers.get(provider)

    def breaker_store(self, provider: str, record: BreakerRecord, now: datetime) -> None:
        with self._lock:
            self._breakers[provider] = record

    def pow_issue(
        self,
        challenge_id: str,
        difficulty: int,
        ip_hash: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> None:
        with self._lock:
            self._pow[challenge_id] = (difficulty, ip_hash, issued_at, expires_at, None)

    def pow_consume(self, challenge_id: str, ip_hash: str, now: datetime) -> bool:
        with self._lock:
            entry = self._pow.get(challenge_id)
            if entry is None:
                return False
            difficulty, stored_ip, issued_at, expires_at, solved_at = entry
            if solved_at is not None or now > expires_at or stored_ip != ip_hash:
                return False
            self._pow[challenge_id] = (difficulty, stored_ip, issued_at, expires_at, now)
            return True

    def pow_purge_expired(self, now: datetime) -> None:
        with self._lock:
            for cid in [
                cid
                for cid, (_d, _ip, _i, expires_at, solved_at) in self._pow.items()
                if solved_at is None and expires_at < now
            ]:
                del self._pow[cid]


@lru_cache(maxsize=1)
def get_abuse_store() -> AbuseStore:
    """Return the process-wide :class:`PostgresAbuseStore` (lazily built).

    The engine is created lazily, so importing this module never requires a live
    database. Unit tests monkeypatch ``app.adviser.router.get_abuse_store`` with
    an :class:`InMemoryAbuseStore`, so this real store is only built when the API
    actually runs against PostgreSQL.
    """

    return PostgresAbuseStore(get_engine())


__all__ = [
    "AbuseStore",
    "DedupeResult",
    "BreakerRecord",
    "PostgresAbuseStore",
    "InMemoryAbuseStore",
    "get_abuse_store",
]
