"""Scheduler service: enqueue heartbeat jobs on a fixed interval.

Run with ``python -m worker.scheduler``. On startup it enqueues one job
immediately (so the queue is exercised within seconds), then enqueues one job
per ``scheduler_interval_seconds`` and beats its own heartbeat each cycle. Like
the worker, it tolerates transient database outages by logging and retrying.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.engine import Engine

from . import queue
from .db import get_engine, wait_for_schema
from .heartbeat import beat
from .runtime import ShutdownSignal
from .settings import get_settings

logger = logging.getLogger("freetier_atlas.scheduler")

SERVICE_NAME = "scheduler"


def enqueue_tick(engine: Engine) -> int:
    """Beat and enqueue one heartbeat job. Returns the new job id."""

    with engine.begin() as conn:
        beat(conn, SERVICE_NAME, detail="tick")
        job_id = queue.enqueue(
            conn,
            "heartbeat",
            {"source": SERVICE_NAME, "ts": datetime.now(UTC).isoformat()},
        )
    logger.info("Enqueued heartbeat job %s", job_id)
    return job_id


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    engine = get_engine()
    shutdown = ShutdownSignal()

    logger.info("Scheduler starting; waiting for schema...")
    if not wait_for_schema(engine):
        logger.warning("Schema not ready before timeout; continuing with retries.")

    while not shutdown.is_set:
        try:
            enqueue_tick(engine)
        except Exception as exc:  # noqa: BLE001 - stay alive across transient DB outages
            logger.warning("Scheduler loop error (%s); retrying.", type(exc).__name__)
        shutdown.wait(settings.scheduler_interval_seconds)

    logger.info("Scheduler stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
