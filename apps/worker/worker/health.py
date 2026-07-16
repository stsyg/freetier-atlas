"""Database-backed liveness check for the non-HTTP worker and scheduler services.

Run as ``python -m worker.health --service worker`` (or ``scheduler``). Exit 0
only when the database is reachable and the named service's heartbeat row is
fresh within the configured staleness threshold; exit 1 otherwise. Output is
actionable but never includes connection strings or credentials, so it is safe
to use as a Docker health check command.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from .db import get_engine
from .heartbeat import SELECT_HEARTBEAT_SQL, is_fresh
from .settings import get_settings


def check_service(service: str) -> tuple[bool, str]:
    """Return ``(healthy, message)`` for ``service`` without leaking secrets."""

    settings = get_settings()
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(SELECT_HEARTBEAT_SQL, {"service": service}).first()
    except Exception as exc:  # noqa: BLE001 - report only the exception type
        return False, f"database unreachable ({type(exc).__name__})"

    if row is None:
        return False, f"no heartbeat recorded for service '{service}'"

    now = datetime.now(UTC)
    if not is_fresh(row.last_beat_at, now, settings.heartbeat_stale_seconds):
        age = (now - row.last_beat_at).total_seconds()
        return (
            False,
            f"heartbeat for '{service}' is stale "
            f"({age:.0f}s > {settings.heartbeat_stale_seconds:.0f}s)",
        )

    return True, f"heartbeat for '{service}' is fresh"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FreeTier Atlas service liveness check.")
    parser.add_argument(
        "--service",
        required=True,
        choices=["worker", "scheduler"],
        help="Which service heartbeat to verify.",
    )
    args = parser.parse_args(argv)

    healthy, message = check_service(args.service)
    print(message)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
