"""Admin data reads/writes over EXISTING catalogue tables (no fabricated data).

The review/contradiction queue and the source-health view are honest
projections of tables that already exist:

* the queue reads the ``review_item`` table (F004 reconciliation writes rows
  needing human review) and its only mutation is advancing ``admin_disposition``
  through the existing ``REVIEW_DISPOSITIONS`` vocabulary; it never invents
  entries; and
* source health reads ``source`` joined to each source's latest ``scan_run`` and
  latest ``snapshot``.

Like the S2 abuse store this uses autonomous ``engine.begin()`` transactions
(independent of the read-only request session) so the single queue mutation
commits safely, and it ships an in-memory backend with identical semantics for
unit tests.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.models.domain import ReviewItem, ScanRun, Snapshot, Source

#: Dispositions an admin may set from the queue (the neutral ``pending`` seed is
#: not a valid target -- an action always moves an item forward).
QUEUE_ACTIONS: tuple[str, ...] = ("approved", "rejected", "deferred")


@dataclass(frozen=True)
class ReviewQueueRow:
    """A review-queue item projected for the admin surface."""

    id: int
    reason: str
    recommended_action: str | None
    admin_disposition: str
    evidence_conflict: dict | None
    candidate_facts: dict | None
    offer_id: int | None
    scan_run_id: int | None
    created_at: datetime


@dataclass(frozen=True)
class SourceHealthRow:
    """A source's health projected from source + latest scan + latest snapshot."""

    source_id: int
    slug: str | None
    adapter_type: str
    official: bool
    enabled: bool
    health: str | None
    endpoint: str | None
    last_scan_status: str | None
    last_scan_finished_at: datetime | None
    last_errors_count: int | None
    last_snapshot_fetched_at: datetime | None


class AdminDataStore(Protocol):
    """The narrow read/mutate seam for admin data over existing tables."""

    def list_review_queue(self, disposition: str | None) -> list[ReviewQueueRow]:
        """Return review-queue items, optionally filtered by ``disposition``."""
        ...

    def set_review_disposition(self, item_id: int, disposition: str, now: datetime) -> bool:
        """Advance an item's disposition; ``False`` if the item does not exist."""
        ...

    def source_health(self) -> list[SourceHealthRow]:
        """Return per-source health projected from existing ingest tables."""
        ...


class PostgresAdminDataStore:
    """PostgreSQL-backed :class:`AdminDataStore` using autonomous transactions."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_review_queue(self, disposition: str | None) -> list[ReviewQueueRow]:
        table = ReviewItem.__table__
        stmt = sa.select(
            table.c.id,
            table.c.reason,
            table.c.recommended_action,
            table.c.admin_disposition,
            table.c.evidence_conflict,
            table.c.candidate_facts,
            table.c.offer_id,
            table.c.scan_run_id,
            table.c.created_at,
        ).order_by(table.c.created_at.desc(), table.c.id.desc())
        if disposition is not None:
            stmt = stmt.where(table.c.admin_disposition == disposition)
        with self._engine.begin() as conn:
            rows = conn.execute(stmt).all()
        return [
            ReviewQueueRow(
                id=int(row.id),
                reason=row.reason,
                recommended_action=row.recommended_action,
                admin_disposition=row.admin_disposition,
                evidence_conflict=row.evidence_conflict,
                candidate_facts=row.candidate_facts,
                offer_id=row.offer_id,
                scan_run_id=row.scan_run_id,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def set_review_disposition(self, item_id: int, disposition: str, now: datetime) -> bool:
        table = ReviewItem.__table__
        stmt = sa.update(table).where(table.c.id == item_id).values(admin_disposition=disposition)
        with self._engine.begin() as conn:
            result = conn.execute(stmt)
        return bool(result.rowcount)

    def source_health(self) -> list[SourceHealthRow]:
        source = Source.__table__
        scan = ScanRun.__table__
        snapshot = Snapshot.__table__

        # Latest finished scan status/errors per source and latest snapshot fetch
        # per source, via correlated scalar subqueries (source scale is small).
        latest_scan = (
            sa.select(scan.c.id)
            .where(scan.c.source_id == source.c.id)
            .order_by(scan.c.started_at.desc(), scan.c.id.desc())
            .limit(1)
            .correlate(source)
            .scalar_subquery()
        )
        latest_snapshot_at = (
            sa.select(sa.func.max(snapshot.c.fetched_at))
            .where(snapshot.c.source_id == source.c.id)
            .correlate(source)
            .scalar_subquery()
        )
        scan_alias = scan.alias("latest_scan")
        stmt = (
            sa.select(
                source.c.id,
                source.c.slug,
                source.c.adapter_type,
                source.c.official,
                source.c.enabled,
                source.c.health,
                source.c.endpoint,
                scan_alias.c.status,
                scan_alias.c.finished_at,
                scan_alias.c.errors_count,
                latest_snapshot_at.label("last_snapshot_fetched_at"),
            )
            .select_from(source.outerjoin(scan_alias, scan_alias.c.id == latest_scan))
            .order_by(source.c.id)
        )
        with self._engine.begin() as conn:
            rows = conn.execute(stmt).all()
        return [
            SourceHealthRow(
                source_id=int(row.id),
                slug=row.slug,
                adapter_type=row.adapter_type,
                official=bool(row.official),
                enabled=bool(row.enabled),
                health=row.health,
                endpoint=row.endpoint,
                last_scan_status=row.status,
                last_scan_finished_at=row.finished_at,
                last_errors_count=(int(row.errors_count) if row.errors_count is not None else None),
                last_snapshot_fetched_at=row.last_snapshot_fetched_at,
            )
            for row in rows
        ]


class InMemoryAdminDataStore:
    """In-memory :class:`AdminDataStore` for unit tests (no database)."""

    def __init__(
        self,
        review_items: list[ReviewQueueRow] | None = None,
        sources: list[SourceHealthRow] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._items: dict[int, ReviewQueueRow] = {item.id: item for item in (review_items or [])}
        self._sources: list[SourceHealthRow] = list(sources or [])

    def list_review_queue(self, disposition: str | None) -> list[ReviewQueueRow]:
        with self._lock:
            items = sorted(self._items.values(), key=lambda i: (i.created_at, i.id), reverse=True)
        if disposition is not None:
            items = [i for i in items if i.admin_disposition == disposition]
        return items

    def set_review_disposition(self, item_id: int, disposition: str, now: datetime) -> bool:
        with self._lock:
            existing = self._items.get(item_id)
            if existing is None:
                return False
            self._items[item_id] = ReviewQueueRow(
                id=existing.id,
                reason=existing.reason,
                recommended_action=existing.recommended_action,
                admin_disposition=disposition,
                evidence_conflict=existing.evidence_conflict,
                candidate_facts=existing.candidate_facts,
                offer_id=existing.offer_id,
                scan_run_id=existing.scan_run_id,
                created_at=existing.created_at,
            )
            return True

    def source_health(self) -> list[SourceHealthRow]:
        with self._lock:
            return list(self._sources)


@lru_cache(maxsize=1)
def get_admin_data_store() -> AdminDataStore:
    """Return the process-wide :class:`PostgresAdminDataStore` (lazily built)."""

    return PostgresAdminDataStore(get_engine())


__all__ = [
    "QUEUE_ACTIONS",
    "ReviewQueueRow",
    "SourceHealthRow",
    "AdminDataStore",
    "PostgresAdminDataStore",
    "InMemoryAdminDataStore",
    "get_admin_data_store",
]
