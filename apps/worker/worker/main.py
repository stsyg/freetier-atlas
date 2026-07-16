"""Worker service: claim and process jobs from the PostgreSQL-backed queue.

Run with ``python -m worker.main``. The worker beats its heartbeat every loop
iteration (so it stays live even while idle), claims the oldest pending job with
``FOR UPDATE SKIP LOCKED``, processes it, and marks it done. A job that raises
during processing is recorded as ``failed`` rather than silently completed. The
loop tolerates transient database outages: it logs and retries instead of
crashing, so the container recovers when PostgreSQL returns.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

from sqlalchemy.engine import Engine

from . import queue
from .db import get_engine, wait_for_schema
from .heartbeat import beat
from .runtime import ShutdownSignal
from .settings import get_settings

logger = logging.getLogger("freetier_atlas.worker")

SERVICE_NAME = "worker"


def process_job(kind: str, payload: dict[str, Any]) -> None:
    """Execute a claimed job.

    Slice 2 only defines the no-op ``heartbeat`` job that proves the queue works
    end to end. Real scan/extraction jobs arrive in F004+. Unknown kinds raise
    so they are recorded as failed instead of silently marked done.
    """

    if kind == "heartbeat":
        logger.info("Processed heartbeat job payload=%s", payload)
        return
    raise ValueError(f"unknown job kind '{kind}'")


def run_once(engine: Engine, worker_id: str) -> bool:
    """Beat, then claim and process one job. Returns ``True`` if a job ran."""

    with engine.begin() as conn:
        beat(conn, SERVICE_NAME, detail=f"worker_id={worker_id}")
        job = queue.claim_next(conn, worker_id)

    if job is None:
        return False

    try:
        process_job(job.kind, job.payload)
    except Exception as exc:  # noqa: BLE001 - any job failure is recorded, not fatal
        logger.warning("Job %s failed: %s", job.id, type(exc).__name__)
        with engine.begin() as conn:
            queue.mark_failed(conn, job.id, f"{type(exc).__name__}: {exc}")
        return True

    with engine.begin() as conn:
        queue.mark_done(conn, job.id)
    logger.info("Completed job %s (kind=%s)", job.id, job.kind)
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    engine = get_engine()
    worker_id = socket.gethostname()
    shutdown = ShutdownSignal()

    logger.info("Worker %s starting; waiting for schema...", worker_id)
    if not wait_for_schema(engine):
        logger.warning("Schema not ready before timeout; continuing with retries.")

    while not shutdown.is_set:
        try:
            processed = run_once(engine, worker_id)
        except Exception as exc:  # noqa: BLE001 - stay alive across transient DB outages
            logger.warning("Worker loop error (%s); retrying.", type(exc).__name__)
            shutdown.wait(settings.worker_poll_interval_seconds)
            continue

        if not processed:
            shutdown.wait(settings.worker_poll_interval_seconds)

    logger.info("Worker %s stopped.", worker_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
