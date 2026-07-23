"""FreeTier Atlas API entrypoint.

Slice 1 of the F002 application scaffold: a minimal FastAPI service that exposes
liveness and readiness health endpoints backed by PostgreSQL. Domain routes,
the worker, the scheduler, and the frontend arrive in later increments.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import __version__
from .db import check_database
from .read_api import router as catalogue_router
from .settings import get_settings

logger = logging.getLogger("freetier_atlas.api")

app = FastAPI(
    title="FreeTier Atlas API",
    version=__version__,
    summary="Evidence-backed catalogue and adviser API (scaffold).",
)

app.include_router(catalogue_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness probe. Returns 200 whenever the process is serving."""

    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": __version__,
        "environment": settings.app_env,
    }


@app.get("/health/ready", tags=["health"])
def health_ready() -> JSONResponse:
    """Readiness probe. Returns 200 when PostgreSQL answers ``SELECT 1``.

    Returns 503 with an actionable, credential-free message otherwise so
    orchestrators and the stack smoke test can detect an unhealthy database.
    """

    try:
        check_database()
    except Exception as exc:  # noqa: BLE001 - surface any DB failure as not-ready
        logger.warning("Readiness check failed: database unreachable (%s)", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "checks": {"database": "unreachable"},
                "detail": "Database connectivity check failed; the API is not ready.",
            },
        )

    return JSONResponse(
        status_code=200,
        content={"status": "ready", "checks": {"database": "ok"}},
    )


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    """Minimal service descriptor."""

    return {"service": get_settings().app_name, "version": __version__, "docs": "/docs"}
