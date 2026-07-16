"""Worker and scheduler settings loaded from environment variables.

Only variable *names* live in the repository. Real values are supplied at
runtime through the environment (see ``.env.example``); no secret is ever
committed. The default ``database_url`` targets the local Docker Compose
``postgres`` service with a non-secret local development credential and must be
overridden in any non-local environment.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """Runtime configuration shared by the worker and scheduler services."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "******postgres:5432/atlas"  # noqa: E501 # pragma: allowlist secret

    # How often the worker polls for a new job when the queue is empty.
    worker_poll_interval_seconds: float = 2.0

    # How often the scheduler enqueues a heartbeat job.
    scheduler_interval_seconds: float = 5.0

    # A service is considered live only when its heartbeat row was updated within
    # this many seconds. Used by ``worker.health`` for the Docker health check.
    heartbeat_stale_seconds: float = 30.0

    # Seconds to wait for a database connection before giving up on one attempt.
    db_connect_timeout_seconds: float = 5.0

    # Bounded wait for the API-applied schema (job_queue table) to appear on
    # startup, so the first loop iterations do not log missing-relation errors.
    schema_wait_timeout_seconds: float = 60.0


@lru_cache(maxsize=1)
def get_settings() -> WorkerSettings:
    """Return a cached :class:`WorkerSettings` instance."""

    return WorkerSettings()
