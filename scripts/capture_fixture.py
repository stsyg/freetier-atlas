#!/usr/bin/env python3
"""Owner-run capture of an official source into a committed fixture (F008 S3).

**This script is the ONLY place in the repository that performs a live fetch.**
It is never invoked by the test suite, by ``scripts/test.ps1``/``check.ps1``, or
by CI -- the whole pipeline stays offline (``OfflineFetcher`` /
``FixtureFetcher``) by default, and a regression test
(``tests/unit/test_no_live_fetcher_in_tests.py``) pins that fact. Run it by hand
when you are adding or refreshing a provider fixture.

Usage::

    python scripts/capture_fixture.py config/examples/providers/<p>.example.yaml \\
        --source <source id> --out tests/fixtures/ingest/<provider>/<adapter>/<case>

It fetches through :class:`~app.ingest.fetch.LiveFetcher` with the provider's own
official-domain allowlist and the standard SSRF/scheme/MIME/size guards, then
writes two files into the output directory:

``source.<ext>``
    The captured document, extension chosen from the source's declared type
    (``html`` -> ``.html``, ``rss`` -> ``.xml``, structured -> ``.json``) so the
    offline runner serves it with the correct MIME.

``capture.json``
    A provenance sidecar::

        {
          "url":            the official URL that was fetched (final, post-redirect),
          "fetched_at":     ISO-8601 UTC timestamp of the capture,
          "http_status":    the HTTP status of the final response,
          "sha256_original": sha256 of the bytes as fetched, before any trimming,
          "sha256_stored":  sha256 of the bytes actually written to source.<ext>,
          "trim_method":    how the excerpt was produced ("none" for a whole document),
          "robots_allowed": the operator's robots.txt/ToS check outcome (true/false/null),
          "tos_note":       a free-text note recording the terms-of-service check,
          "captured_by":    who ran the capture
        }

    CI validates that the sidecar is **present** and that ``sha256_stored``
    matches the bytes on disk. It deliberately never asserts *freshness*: a
    "fixture must be newer than N days" check in CI is a time bomb that breaks
    the build on a calendar boundary rather than on a real defect. Freshness is
    asserted at **runtime** by ``assess_staleness``, which withholds publication
    for a stale source -- which is where it belongs.

**Copyright posture (decision Q2-A).** Commit only **minimal official excerpts**
-- the specific table or section the extraction profile reads -- with attribution
in ``tests/fixtures/ingest/README.md``. Do **not** mirror whole pages or bulk
content. The script prints this warning on every run and does not trim for you:
trimming is a judgement call you make, and record in ``trim_method``.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "apps" / "api"))

from app.config.loader import load_and_validate  # noqa: E402
from app.config.models import ProviderConfig  # noqa: E402
from app.ingest.fetch import FetchError, FetchPolicy, LiveFetcher  # noqa: E402
from app.ingest.runner import FIXTURE_EXTENSIONS_BY_SOURCE_TYPE, fetch_policy_for  # noqa: E402

EXCERPT_WARNING = (
    "REMINDER (decision Q2-A): commit only MINIMAL OFFICIAL EXCERPTS -- the specific\n"
    "table or section the extraction profile reads -- with attribution in\n"
    "tests/fixtures/ingest/README.md. Do NOT mirror whole pages or bulk content.\n"
    "Trim the captured file by hand and record how in capture.json's 'trim_method'."
)

#: The sidecar keys every committed fixture must carry.
CAPTURE_SIDECAR_FIELDS: tuple[str, ...] = (
    "url",
    "fetched_at",
    "http_status",
    "sha256_original",
    "sha256_stored",
    "trim_method",
    "robots_allowed",
    "tos_note",
    "captured_by",
)


def sha256_hex(data: bytes) -> str:
    """SHA-256 hex digest of ``data`` (the same hash the sidecar records)."""

    return hashlib.sha256(data).hexdigest()


def extension_for(source_type: str) -> str:
    """Return the fixture file extension for a source of ``source_type``."""

    extensions = FIXTURE_EXTENSIONS_BY_SOURCE_TYPE.get(source_type.lower())
    if not extensions:
        raise SystemExit(
            f"error: source type '{source_type}' has no capturable document "
            f"(capturable types: "
            f"{sorted(k for k, v in FIXTURE_EXTENSIONS_BY_SOURCE_TYPE.items() if v)})."
        )
    return extensions[0]


def build_sidecar(
    *,
    url: str,
    fetched_at: datetime,
    http_status: int,
    original: bytes,
    stored: bytes,
    trim_method: str,
    robots_allowed: bool | None,
    tos_note: str,
    captured_by: str,
) -> dict[str, object]:
    """Build the ``capture.json`` provenance record."""

    return {
        "url": url,
        "fetched_at": fetched_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "http_status": http_status,
        "sha256_original": sha256_hex(original),
        "sha256_stored": sha256_hex(stored),
        "trim_method": trim_method,
        "robots_allowed": robots_allowed,
        "tos_note": tos_note,
        "captured_by": captured_by,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/capture_fixture.py",
        description=(
            "OWNER-RUN ONLY. Capture one official source into a committed fixture "
            "plus a capture.json provenance sidecar. Never invoked by tests or CI."
        ),
        epilog=EXCERPT_WARNING,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("config", help="Provider YAML config file.")
    parser.add_argument("--source", required=True, help="The source id to capture.")
    parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="Output directory, conventionally tests/fixtures/ingest/<provider>/<adapter>/<case>/.",
    )
    parser.add_argument(
        "--trim-method",
        default="none",
        help="How the committed excerpt was produced (recorded verbatim in capture.json).",
    )
    parser.add_argument(
        "--robots-allowed",
        choices=("yes", "no", "unknown"),
        default="unknown",
        help="Your robots.txt check outcome. 'unknown' records null -- never a guess.",
    )
    parser.add_argument(
        "--tos-note",
        default="",
        help="Free-text note recording the terms-of-service check for this source.",
    )
    parser.add_argument(
        "--captured-by",
        default=None,
        help="Who ran the capture (defaults to the current OS user).",
    )
    parser.add_argument(
        "--yes-i-am-the-owner",
        action="store_true",
        help=(
            "Required. Acknowledges that this performs a LIVE network fetch and that "
            "you will commit only a minimal official excerpt with attribution."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    print(EXCERPT_WARNING, file=sys.stderr)
    if not args.yes_i_am_the_owner:
        print(
            "\nerror: refusing to fetch. This script performs LIVE network access and is "
            "owner-run only.\n       Re-run with --yes-i-am-the-owner once you have read the "
            "reminder above.",
            file=sys.stderr,
        )
        return 2

    model = load_and_validate(args.config)
    if not isinstance(model, ProviderConfig):
        print(f"error: {args.config} is not a provider config.", file=sys.stderr)
        return 2

    source = next((s for s in model.sources if s.id == args.source), None)
    if source is None:
        print(
            f"error: no source '{args.source}' in {args.config}; "
            f"known: {sorted(s.id for s in model.sources)}.",
            file=sys.stderr,
        )
        return 2
    if not source.url:
        print(f"error: source '{source.id}' has no url to capture.", file=sys.stderr)
        return 2

    extension = extension_for(source.type)
    policy: FetchPolicy = fetch_policy_for(model)

    # The single sanctioned live-fetch construction in the repository. The policy
    # is the provider's own official-domain allowlist, so a URL outside it is
    # refused before any socket is opened.
    fetcher = LiveFetcher(policy, enable_network=True)
    try:
        result = fetcher.fetch(source.url)
    except FetchError as exc:
        print(f"error: fetch refused/failed ({exc.reason}): {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    document = out_dir / f"source.{extension}"
    document.write_bytes(result.content)

    sidecar = build_sidecar(
        url=result.final_url,
        fetched_at=result.fetched_at,
        http_status=result.status,
        original=result.content,
        stored=result.content,
        trim_method=args.trim_method,
        robots_allowed={"yes": True, "no": False, "unknown": None}[args.robots_allowed],
        tos_note=args.tos_note,
        captured_by=args.captured_by or getpass.getuser(),
    )
    (out_dir / "capture.json").write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"wrote {document} ({len(result.content)} bytes) and {out_dir / 'capture.json'}")
    print(
        "NEXT: trim the document to the minimal official excerpt the extraction profile\n"
        "      reads, update capture.json's 'trim_method' AND 'sha256_stored' (the\n"
        "      integrity test compares it to the bytes on disk), add the attribution to\n"
        "      tests/fixtures/ingest/README.md, and write the case's expected.json."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - owner-run CLI shell
    raise SystemExit(main())
