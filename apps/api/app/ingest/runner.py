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
  default. Fixtures are multi-format (F008 S3): a source is served
  ``source.html`` / ``source.json`` / ``source.xml`` with the MIME derived from
  its **declared** ``type``, and an unresolvable source is left unregistered
  rather than served a guessed content type.

The library function leaves the transaction to its caller (it uses SAVEPOINTs +
``flush``). The ``__main__`` CLI owns a session and commits once at the end
unless ``--dry-run`` is given.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config.loader import load_and_validate
from app.config.models import ProviderConfig
from app.ingest.config_sync import SyncResult, sync_provider
from app.ingest.fetch import Fetcher, FetchPolicy, FixtureFetcher, OfflineFetcher
from app.ingest.reconcile import reconcile_scan
from app.ingest.scan import run_scan
from app.models.domain import Provider, ScanRun, Source

_HTML_MIME = "text/html"
_JSON_MIME = "application/json"
_RSS_MIME = "application/rss+xml"
_XML_MIME = "application/xml"

#: MIME type served for each supported fixture extension. ``xml`` is refined by
#: the source's declared type (an ``rss`` source serves ``application/rss+xml``),
#: because a feed and a generic XML document are different content types to the
#: fetch policy's MIME allowlist.
FIXTURE_MIME_BY_EXTENSION: dict[str, str] = {
    "html": _HTML_MIME,
    "json": _JSON_MIME,
    "xml": _XML_MIME,
}

#: Which fixture extension(s) a source of each declared ``type`` may be served
#: from. Keyed by the config ``source.type`` (== the ORM ``adapter_type``).
#: ``mcp`` sources carry no URL and are never fixture-registered. A source whose
#: type is not listed here falls back to "any known extension, but only if the
#: choice is unambiguous" -- see :func:`resolve_fixture_path`.
FIXTURE_EXTENSIONS_BY_SOURCE_TYPE: dict[str, tuple[str, ...]] = {
    "html": ("html",),
    "rss": ("xml",),
    "reference-json": ("json",),
    "structured-api": ("json",),
    "mcp": (),
}


def fixture_mime_for(source_type: str | None, extension: str) -> str | None:
    """Return the MIME type to serve a ``extension`` fixture for ``source_type``.

    Returns ``None`` for an extension we do not recognise -- the caller then
    declines to register the fixture rather than guessing a content type.
    """

    mime = FIXTURE_MIME_BY_EXTENSION.get(extension.lower().lstrip("."))
    if mime is None:
        return None
    if mime == _XML_MIME and (source_type or "").lower() == "rss":
        return _RSS_MIME
    return mime


def resolve_fixture_path(
    directory: Path, source_id: str, source_type: str | None
) -> tuple[Path, str] | None:
    """Resolve the captured fixture for one source, or ``None`` if there is none.

    Two layouts are accepted for each candidate extension, nested first (the
    layout the capture script writes and the extraction fixtures already use)
    and then flat::

        <directory>/<source id>/source.<ext>
        <directory>/<source id>.<ext>

    The candidate extensions come from the source's declared ``type``
    (:data:`FIXTURE_EXTENSIONS_BY_SOURCE_TYPE`), so an ``html`` source is served
    HTML and an ``rss`` source is served XML -- the format is *declared*, never
    sniffed. A source type we do not know falls back to trying every known
    extension, but an **ambiguous** match (more than one extension present)
    resolves to ``None``: "unknown is better than guessed" outranks convenience,
    so an unresolvable source is simply not registered and the fetcher reports it
    as not-found rather than serving a coin-flip.
    """

    declared = FIXTURE_EXTENSIONS_BY_SOURCE_TYPE.get((source_type or "").lower())
    if declared is not None and not declared:
        return None  # e.g. mcp: no URL-backed document to serve
    candidates = declared if declared else tuple(FIXTURE_MIME_BY_EXTENSION)

    found: list[tuple[Path, str]] = []
    for extension in candidates:
        nested = directory / source_id / f"source.{extension}"
        flat = directory / f"{source_id}.{extension}"
        for path in (nested, flat):
            if path.is_file():
                found.append((path, extension))
                break

    if not found:
        return None
    if declared is None and len(found) > 1:
        # Undeclared type with several possible fixtures: refuse to guess.
        return None
    return found[0]


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
    published: int = 0
    publish_unchanged: int = 0
    reviewed: int = 0
    withheld: int = 0
    publish_error: str | None = None
    error: str | None = None


@dataclass
class RunnerResult:
    """Summary of one :func:`run_provider_scans` invocation."""

    provider_slug: str
    configured_sources: int | None = None
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

    @property
    def total_published(self) -> int:
        return sum(s.published for s in self.sources)

    @property
    def total_reviewed(self) -> int:
        return sum(s.reviewed for s in self.sources)


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
    registered under that URL if one can be resolved in ``fixtures_dir`` -- either
    a nested ``<source id>/source.<ext>`` file or a flat ``<source id>.<ext>``
    file, where ``<ext>`` is chosen from the source's declared ``type``
    (``html`` -> ``source.html`` as ``text/html``; ``rss`` -> ``source.xml`` as
    ``application/rss+xml``; ``structured-api`` / ``reference-json`` ->
    ``source.json`` as ``application/json``). The MIME is therefore *derived from
    the declaration*, never sniffed from the bytes and never defaulted.

    Sources whose fixture cannot be resolved unambiguously are simply not
    registered, so the fetcher reports them as not-found (a graceful per-source
    error) rather than reaching the network or serving a guessed content type.

    **Fixture-root convention.** ``fixtures_dir`` is one adapter directory of one
    provider: ``tests/fixtures/ingest/<provider>/<adapter>/``. Each source (or
    case) is a child directory holding ``source.<ext>`` next to its
    ``expected.json`` and ``capture.json`` sidecar.
    """

    directory = Path(fixtures_dir)
    fixtures: dict[str, tuple[bytes, str]] = {}
    for source in config.sources:
        if not source.url:
            continue
        resolved = resolve_fixture_path(directory, source.id, source.type)
        if resolved is None:
            continue
        path, extension = resolved
        mime = fixture_mime_for(source.type, extension)
        if mime is None:  # unknown extension: decline rather than guess
            continue
        fixtures[source.url] = (path.read_bytes(), mime)
    return FixtureFetcher(fixtures, policy or fetch_policy_for(config))


def run_provider_scans(
    session: Session,
    config: ProviderConfig,
    fetcher: Fetcher,
    *,
    reconcile: bool = True,
    sync: bool = True,
    publish: bool = False,
) -> RunnerResult:
    """Sync ``config`` then scan (and optionally reconcile / publish) each source.

    Returns a per-source summary. Each source runs in its own SAVEPOINT so a
    build/scan fault is isolated as a per-source error. The caller owns the
    surrounding transaction (this flushes / uses nested transactions but never
    commits).

    When ``publish`` is true a second phase runs the deterministic publication
    gate (:func:`app.publish.publisher.publish_scan`) over every scanned source.
    Publication is deliberately a *separate* phase that runs only after **all**
    sources have been scanned and reconciled, so cross-source contradictions
    already exist as pending review items and the gate never auto-publishes a
    contradicted offer. When ``publish`` is false the runner writes no
    ``offer`` / ``offer_version`` / ``quota`` rows at all (F004/F005-slice-1
    behaviour is preserved).
    """

    sync_result = sync_provider(session, config) if sync else None

    provider = session.execute(
        select(Provider).where(Provider.slug == config.provider.id)
    ).scalar_one()
    source_query = select(Source).where(
        Source.provider_id == provider.id,
        Source.enabled.is_(True),
    )
    if sync:
        # A synchronized run scans exactly what the current config declares.
        # Historical rows are retained by additive source sync but cannot leak
        # into a coverage-only provider run. With sync=False, callers explicitly
        # opt into scanning already-seeded database sources.
        source_query = source_query.where(Source.slug.in_(source.id for source in config.sources))
    sources = session.execute(source_query.order_by(Source.slug)).scalars().all()

    result = RunnerResult(
        provider_slug=config.provider.id,
        configured_sources=len(config.sources),
        sync=sync_result,
    )
    # Phase 1: scan + reconcile every source (isolated per-source savepoints).
    scanned: list[tuple[Source, ScanRun, int]] = []
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
        scanned.append((source, scan_run, len(result.sources) - 1))

    # Phase 2: publication gate, only after every source is reconciled.
    if publish:
        # Imported lazily to avoid an import cycle (app.publish.publisher imports
        # reconcile helpers from app.ingest, whose package __init__ imports this
        # runner module).
        from app.publish.publisher import publish_scan

        for source, scan_run, index in scanned:
            savepoint = session.begin_nested()
            try:
                publish_result = publish_scan(
                    session,
                    scan_run,
                    source,
                    config.publishing,
                    service_categories=config.service_categories,
                )
                savepoint.commit()
            except Exception as exc:  # noqa: BLE001 - isolate one source's publish fault
                savepoint.rollback()
                result.sources[index] = replace(
                    result.sources[index],
                    publish_error=f"{type(exc).__name__}: {exc}",
                )
                continue
            result.sources[index] = replace(
                result.sources[index],
                published=publish_result.published,
                publish_unchanged=publish_result.unchanged,
                reviewed=publish_result.reviewed,
                withheld=publish_result.withheld,
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
            "Directory of captured official fixtures for this provider+adapter "
            "(convention: tests/fixtures/ingest/<provider>/<adapter>/). Each "
            "source resolves to '<source id>/source.<ext>' or '<source id>.<ext>' "
            "where <ext> follows the source's declared type (html -> .html, "
            "rss -> .xml, structured-api/reference-json -> .json). When given, a "
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
        "--publish",
        action="store_true",
        help=(
            "After scanning and reconciling, run the deterministic publication "
            "gate: high-confidence official offers are published (offer + "
            "immutable offer_version + quota + classified Z0), uncertain or "
            "contradictory evidence is held as a pending review item, and "
            "unofficial/unevidenced data is withheld. Off by default."
        ),
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
    if result.configured_sources == 0:
        lines.append("  scans: zero configured sources; sync and coverage completed")
    for outcome in result.sources:
        if outcome.status == "scanned":
            line = (
                f"  [{outcome.scan_status}] {outcome.slug}: "
                f"documents={outcome.documents} candidates={outcome.candidates} "
                f"changes={outcome.changes} errors={outcome.errors}"
            )
            if outcome.publish_error is not None:
                line += f" publish-error={outcome.publish_error}"
            elif (
                outcome.published
                or outcome.reviewed
                or outcome.withheld
                or outcome.publish_unchanged
            ):
                line += (
                    f" published={outcome.published} unchanged={outcome.publish_unchanged} "
                    f"reviewed={outcome.reviewed} withheld={outcome.withheld}"
                )
            lines.append(line)
        else:
            lines.append(f"  [error] {outcome.slug}: {outcome.error}")
    lines.append(
        f"  totals: scanned={result.scanned} failed={result.failed} "
        f"published={result.total_published} reviewed={result.total_reviewed}"
    )
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
                    session,
                    model,
                    fetcher,
                    reconcile=not args.no_reconcile,
                    publish=args.publish,
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
    "resolve_fixture_path",
    "fixture_mime_for",
    "FIXTURE_MIME_BY_EXTENSION",
    "FIXTURE_EXTENSIONS_BY_SOURCE_TYPE",
    "run_provider_scans",
    "main",
)


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    raise SystemExit(main())
