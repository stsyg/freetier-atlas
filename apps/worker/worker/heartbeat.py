"""Service heartbeats used for database-backed liveness health checks.

Each service upserts a single row keyed by its name, so restarts never create
duplicate rows. The ``worker.health`` command reads these rows to decide whether
a non-HTTP service is live.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

UPSERT_HEARTBEAT_SQL = text(
    """
    INSERT INTO service_heartbeat (service, last_beat_at, detail)
    VALUES (:service, now(), :detail)
    ON CONFLICT (service) DO UPDATE
    SET last_beat_at = now(), detail = EXCLUDED.detail
    """
)

SELECT_HEARTBEAT_SQL = text(
    "SELECT service, last_beat_at, detail FROM service_heartbeat WHERE service = :service"
)


def beat(conn: Connection, service: str, detail: str = "") -> None:
    """Record (insert or update) the heartbeat for ``service``."""

    conn.execute(UPSERT_HEARTBEAT_SQL, {"service": service, "detail": detail})


def is_fresh(last_beat_at: datetime, now: datetime, stale_seconds: float) -> bool:
    """Return ``True`` when ``last_beat_at`` is within ``stale_seconds`` of ``now``.

    Pure function (no database) so it can be unit-tested directly. A future
    ``last_beat_at`` (clock skew) is treated as fresh.
    """

    return now - last_beat_at <= timedelta(seconds=stale_seconds)
