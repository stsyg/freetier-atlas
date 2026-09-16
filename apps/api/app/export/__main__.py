"""CLI: ``python -m app.export --out DIR [--as-of ISO8601]``.

The wall clock is consulted at exactly one place -- this boundary -- and only to
default ``--as-of`` when the caller does not pin it. The resolved instant is then
injected into :func:`app.export.catalogue.build_catalogue_export` and stamped
into every artefact, so the core stays pure and a published snapshot can be
reproduced byte-for-byte by re-running with the same ``--as-of`` against the same
database state.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from app.db import get_sessionmaker

from .catalogue import build_catalogue_export, write_export


def _parse_as_of(text: str) -> datetime:
    """Parse an ISO-8601 ``--as-of``; a naive value is read as UTC."""

    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.export",
        description="Export the read-only catalogue to a deterministic static JSON snapshot.",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory for the snapshot (created if absent).",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="ISO-8601 instant to freeze evidence currency at (default: now, UTC).",
    )
    args = parser.parse_args(argv)

    as_of = _parse_as_of(args.as_of) if args.as_of else datetime.now(UTC)

    session = get_sessionmaker()()
    try:
        artefacts = build_catalogue_export(session, as_of=as_of)
    finally:
        # Read-only: never commit. Roll back so no session state can leak out.
        session.rollback()
        session.close()

    written = write_export(artefacts, Path(args.out))
    stamp = as_of.astimezone(UTC).isoformat()
    print(f"Wrote {len(written)} artefacts to {args.out} (as_of={stamp})")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI boundary
    raise SystemExit(main())
