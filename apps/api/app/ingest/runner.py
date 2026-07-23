"""Provider scan runner and runtime entrypoint (F005 slice 1).

Composes the existing, separately-tested ingestion steps into one invocable
runtime driver (docs/ARCHITECTURE.md "Ingestion pipeline"):

    config -> sync_provider -> [per source] run_scan -> reconcile_scan

:func:`run_provider_scans` syncs a provider configuration into ``provider`` /
``source`` rows (idempotently, via :func:`app.ingest.config_sync.sync_provider`)
and then drives each enabled source through :func:`app.ingest.scan.run_scan`
and :func:`app.ingest.reconcile.reconcile_scan`. Each source runs inside its own
``SAVEPOINT`` so a fault in one source (for example an adapter whose parser
profile is unset, which raises at *build* time) is isolated as a per-source
error and never aborts the whole run.

Hard invariants carried from the pipeline it composes:

* **No publication path.** It only ever writes the pre-publication rows the
  scan/reconcile steps write (``scan_run`` / ``snapshot`` / ``candidate`` /
  official ``evidence`` / ``discovery_candidate`` / draft ``change_event`` /
  ``review_item``). It never creates or mutates ``offer`` / ``offer_version`` /
  ``quota``; every official ``evidence`` row it produces has
  ``offer_version_id IS NULL``.
* **Network only through the Fetcher seam.** The default fetcher is
  :class:`~app.ingest.fetch.OfflineFetcher` (no egress). ``--fixtures`` builds a
  :class:`~app.ingest.fetch.FixtureFetcher` from captured official snapshots so
  extraction is deterministic and offline; ``LiveFetcher`` stays disabled by
  default.

The library function leaves the transaction to its caller (it uses SAVEPOINTs +
``flush``). The ``__main__`` CLI owns a session and commits once at the end
unless ``--dry-run`` is given.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config.loader import load_and_validate
from app.config.models import ProviderConfig
from app.ingest.config_sync import SyncResult, sync_provider
from app.ingest.fetch import Fetcher, FetchPolicy, FixtureFetcher, OfflineFetcher
from app.ingest.reconcile import reconcile_scan
from app.ingest.scan import run_scan
from app.models.domain import Provider, Source

_DEFAULT_MIME = "text/html"


@dataclass(frozen=True)
class SourceScanOutcome:
    """The result of scanning one source through the runner."""

    slug: str | None
    status: str  # "scanned" | "error"
    documents: int = 0
    candidates: int = 0
    changes: int = 0
    errors: int = 0
    scan_run_id: int | None = None
    scan_status: str | None = None
    reconcile_added: int = 0
    reconcile_modified: int = 0
    error: str | None = None


@dataclass
class RunnerResult:
    """Summary of one :func:`run_provider_scans` invocation."""

    provider_slug: str
    sync: SyncResult | None = None
    sources: list[SourceScanOutcome] = field(default_factory=list)

    @property
    def scanned(self) -> int:
        return sum(1 for s in self.sources if s.status == "scanned")

    @property
    def failed(self) -> int:
        return sum(1 for s in self.sources if s.status == "error")

    @property
    def total_candidates(self) -> int:
        return sum(s.candidates for s in self.sources)


def fetch_policy_for(config: ProviderConfig) -> FetchPolicy:
    """Build a :class:`FetchPolicy` allowlisting the provider's official domains."""

    return FetchPolicy(official_domains=tuple(config.provider.official_domains))


def build_fixture_fetcher(
    config: ProviderConfig,
    fixtures_dir: str | Path,
    *,
    policy: FetchPolicy | None = None,
) -> FixtureFetcher:
    """Build a :class:`FixtureFetcher` mapping each source URL to a captured file.

    For every source in ``config`` that has a ``url``, a captured fixture is
    registered under that URL if one exists in ``fixtures_dir`` -- either a flat
    ``<source id>.html`` file or a ``<source id>/source.html`` file (the layout
    the extraction fixtures already use). Sources without a matching fixture are
    simply not registered, so the fetcher reports them as not-found (a graceful
    per-source error) rather than reaching the network.
    """

    directory = Path(fixtures_dir)
    fixtures: dict[str, tuple[bytes, str]] = {}
    for source in config.sources:
        if not source.url:
            continue
        flat = directory / f"{source.id}.html"
        nested = directory / source.id / "source.html"
        path = flat if flat.is_file() else nested
        if path.is_file():
            fixtures[source.url] = (path.read_bytes(), _DEFAULT_MIME)
    return FixtureFetcher(fixtures, policy or fetch_policy_for(config))


def run_provider_scans(
    session: Session,
    config: ProviderConfig,
    fetcher: Fetcher,
    *,
    reconcile: bool = True,
    sync: bool = True,
) -> RunnerResult:
    """Sync ``config`` then scan (and optionally reconcile) each enabled source.

    Returns a per-source summary. Each source runs in its own SAVEPOINT so a
    build/scan fault is isolated as a per-source error. The caller owns the
    surrounding transaction (this flushes / uses nested transactions but never
    commits).
    """

    sync_result = sync_provider(session, config) if sync else None

    provider = session.execute(
        select(Provider).where(Provider.slug == config.provider.id)
    ).scalar_one()
    sources = (
        session.execute(
            select(Source)
            .where(Source.provider_id == provider.id, Source.enabled.is_(True))
            .order_by(Source.slug)
        )
        .scalars()
        .all()
    )

    result = RunnerResult(provider_slug=config.provider.id, sync=sync_result)
    for source in sources:
        savepoint = session.begin_nested()
        try:
            scan_run = run_scan(source, fetcher, session)
            reconcile_result = reconcile_scan(scan_run, source, session) if reconcile else None
            savepoint.commit()
        except Exception as exc:  # noqa: BLE001 - isolate one source's fault
            savepoint.rollback()
            result.sources.append(
                SourceScanOutcome(
                    slug=source.slug,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        result.sources.append(
            SourceScanOutcome(
                slug=source.slug,
                status="scanned",
                documents=scan_run.documents_count,
                candidates=scan_run.candidates_count,
                changes=scan_run.changes_count,
                errors=scan_run.errors_count,
                scan_run_id=scan_run.id,
                scan_status=scan_run.status,
                reconcile_added=reconcile_result.added if reconcile_result else 0,
                reconcile_modified=reconcile_result.modified if reconcile_result else 0,
            )
        )
    return result


# --- CLI entrypoint --------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingest.runner",
        description=(
            "Sync a provider config into the database and run offline ingestion "
            "scans (Candidate + official Evidence only; no publication path)."
        ),
    )
    parser.add_argument(
        "configs",
        nargs="+",
        help="One or more provider YAML config files to sync and scan.",
    )
    parser.add_argument(
        "--fixtures",
        metavar="DIR",
        default=None,
        help=(
            "Directory of captured '<source id>.html' fixtures. When given, a "
            "FixtureFetcher serves those offline; otherwise the safe OfflineFetcher "
            "is used (no network egress)."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy database URL (defaults to the DATABASE_URL environment variable).",
    )
    parser.add_argument(
        "--no-reconcile",
        action="store_true",
        help="Run scans only; skip the reconciliation pass.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Roll back instead of committing (inspect results without persisting).",
    )
    return parser


def _fetcher_for(config: ProviderConfig, fixtures_dir: str | None) -> Fetcher:
    if fixtures_dir:
        return build_fixture_fetcher(config, fixtures_dir)
    return OfflineFetcher(fetch_policy_for(config))


def _format_result(result: RunnerResult) -> str:
    lines = [f"provider '{result.provider_slug}':"]
    if result.sync is not None:
        lines.append(
            f"  sync: provider={result.sync.provider_action} "
            f"sources created={result.sync.created} updated={result.sync.updated} "
            f"unchanged={result.sync.unchanged}"
        )
    for outcome in result.sources:
        if outcome.status == "scanned":
            lines.append(
                f"  [{outcome.scan_status}] {outcome.slug}: "
                f"documents={outcome.documents} candidates={outcome.candidates} "
                f"changes={outcome.changes} errors={outcome.errors}"
            )
        else:
            lines.append(f"  [error] {outcome.slug}: {outcome.error}")
    lines.append(f"  totals: scanned={result.scanned} failed={result.failed}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "error: no database URL (pass --database-url or set DATABASE_URL).",
            file=sys.stderr,
        )
        return 2

    engine = create_engine(database_url)
    exit_code = 0
    try:
        with Session(engine) as session:
            for config_path in args.configs:
                model = load_and_validate(config_path)
                if not isinstance(model, ProviderConfig):
                    print(
                        f"error: {config_path} is not a provider config "
                        f"(got {type(model).__name__}).",
                        file=sys.stderr,
                    )
                    exit_code = 2
                    continue
                fetcher = _fetcher_for(model, args.fixtures)
                result = run_provider_scans(
                    session, model, fetcher, reconcile=not args.no_reconcile
                )
                print(_format_result(result))
                if result.failed:
                    exit_code = max(exit_code, 1)
            if args.dry_run:
                session.rollback()
                print("(dry-run: rolled back, nothing persisted)")
            else:
                session.commit()
    finally:
        engine.dispose()
    return exit_code


__all__: Sequence[str] = (
    "SourceScanOutcome",
    "RunnerResult",
    "fetch_policy_for",
    "build_fixture_fetcher",
    "run_provider_scans",
    "main",
)


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    raise SystemExit(main())
