"""PostgreSQL-backed job queue operations.

The queue is the architecture's "PostgreSQL-backed queue initially" implemented
with plain SQL (no external broker). A worker claims one pending job at a time
using ``SELECT ... FOR UPDATE SKIP LOCKED`` so concurrent workers never
double-process a job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Valid job lifecycle states.
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
VALID_STATUSES = frozenset({STATUS_PENDING, STATUS_RUNNING, STATUS_DONE, STATUS_FAILED})

ENQUEUE_SQL = text(
    """
    INSERT INTO job_queue (kind, payload, status)
    VALUES (:kind, CAST(:payload AS JSONB), 'pending')
    RETURNING id
    """
)

# Atomically claim the oldest pending job. FOR UPDATE SKIP LOCKED ensures two
# workers never grab the same row.
CLAIM_SQL = text(
    """
    UPDATE job_queue
    SET status = 'running',
        started_at = now(),
        attempts = attempts + 1,
        locked_by = :worker_id
    WHERE id = (
        SELECT id
        FROM job_queue
        WHERE status = 'pending'
        ORDER BY enqueued_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, kind, payload, attempts
    """
)

MARK_DONE_SQL = text(
    """
    UPDATE job_queue
    SET status = 'done', finished_at = now(), last_error = NULL
    WHERE id = :id
    """
)

MARK_FAILED_SQL = text(
    """
    UPDATE job_queue
    SET status = 'failed', finished_at = now(), last_error = :error
    WHERE id = :id
    """
)


@dataclass(frozen=True)
class ClaimedJob:
    """A job claimed by a worker."""

    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int


def next_status(current: str, *, success: bool) -> str:
    """Pure state transition for a running job. Used by unit tests.

    A running job moves to ``done`` on success or ``failed`` otherwise. Any
    other current state is invalid to complete.
    """

    if current != STATUS_RUNNING:
        raise ValueError(f"cannot complete a job in state '{current}'")
    return STATUS_DONE if success else STATUS_FAILED


def enqueue(conn: Connection, kind: str, payload: dict[str, Any] | None = None) -> int:
    """Insert a pending job and return its id."""

    encoded = json.dumps(payload or {})
    result = conn.execute(ENQUEUE_SQL, {"kind": kind, "payload": encoded})
    return int(result.scalar_one())


def claim_next(conn: Connection, worker_id: str) -> ClaimedJob | None:
    """Claim the oldest pending job, or return ``None`` when the queue is empty."""

    row = conn.execute(CLAIM_SQL, {"worker_id": worker_id}).first()
    if row is None:
        return None
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload or "{}")
    return ClaimedJob(id=int(row.id), kind=row.kind, payload=payload, attempts=int(row.attempts))


def mark_done(conn: Connection, job_id: int) -> None:
    """Mark a claimed job as successfully completed."""

    conn.execute(MARK_DONE_SQL, {"id": job_id})


def mark_failed(conn: Connection, job_id: int, error: str) -> None:
    """Mark a claimed job as failed, recording a truncated error message."""

    conn.execute(MARK_FAILED_SQL, {"id": job_id, "error": error[:500]})
