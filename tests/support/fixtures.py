"""Shared multi-format fixture harness for provider ingestion tests (F008 S3).

This is the seam that lets a provider slice add **only data**. Drop a captured
official excerpt and its ``expected.json`` into
``tests/fixtures/ingest/<provider>/<adapter>/<case>/`` and the helpers here will
load it, drive the right adapter through a :class:`FixtureFetcher`, and assert
the extracted facts, the evidence locations and a stable ``content_hash`` -- with
no new test code per provider.

**Case vocabulary** (:data:`CASES`, extending ``docs/TEST_STRATEGY.md``): the
five extraction cases ``unchanged | changed | partial | malformed |
contradictory`` plus the two F008 *pipeline* cases ``withdrawn | stale``. The
first five are pure extraction shapes provable from one document; the last two
are only meaningful across time, so they are driven end-to-end against real ORM
rows by :func:`drive_withdrawn` and :func:`drive_stale`.

**Determinism.** Both pipeline helpers are **time-injected**: they pass an
explicit ``now`` into :func:`~app.ingest.reconcile.reconcile_scan` /
:func:`~app.publish.publisher.publish_scan` and derive it from the snapshot the
fixture produced, never from the wall clock and never with ``sleep()``. The same
inputs therefore yield the same rows on every run, on any machine, at any hour.

**Offline.** Everything here goes through :class:`FixtureFetcher`; no
``LiveFetcher`` is imported and no socket is ever opened.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.ingest.adapters import (
    HtmlDocAdapter,
    RssFeedAdapter,
    StructuredApiAdapter,
    resolve_json_profile,
    resolve_profile,
)
from app.ingest.base import CandidateFacts, SourceAdapter
from app.ingest.fetch import FetchPolicy, FixtureFetcher
from app.ingest.reconcile import (
    DEFAULT_STALENESS_WINDOW,
    StalenessAssessment,
    assess_staleness,
    parse_schedule_window,
    reconcile_scan,
)
from app.ingest.reference import JsonOfferAdapter
from app.ingest.runner import fixture_mime_for
from app.ingest.scan import _content_hash, run_scan
from app.models.domain import Candidate, ChangeEvent, ReviewItem, Source
from sqlalchemy import select
from sqlalchemy.orm import Session

#: The full case vocabulary. The first five are single-document extraction
#: shapes; ``withdrawn`` and ``stale`` are cross-scan pipeline cases (F008 S3).
CASES: tuple[str, ...] = (
    "unchanged",
    "changed",
    "partial",
    "malformed",
    "contradictory",
    "withdrawn",
    "stale",
)

#: The subset provable from a single captured document.
EXTRACTION_CASES: tuple[str, ...] = CASES[:5]

#: The subset that only exists across time and needs real persisted rows.
PIPELINE_CASES: tuple[str, ...] = CASES[5:]

#: Root of the committed ingestion fixture corpus.
FIXTURE_ROOT: Path = Path(__file__).resolve().parents[1] / "fixtures" / "ingest"

#: Fixture file extension per adapter directory name.
_ADAPTER_EXTENSION: dict[str, str] = {
    "html": "html",
    "rss": "xml",
    "structured": "json",
    "mcp": "json",
    "reference-json": "json",
}

#: The source ``type`` each adapter directory stands for (drives the MIME).
_ADAPTER_SOURCE_TYPE: dict[str, str] = {
    "html": "html",
    "rss": "rss",
    "structured": "structured-api",
    "mcp": "mcp",
    "reference-json": "reference-json",
}


class FixtureCaseError(AssertionError):
    """Raised when a fixture case is missing or malformed on disk."""


@dataclass(frozen=True)
class FixtureCase:
    """One loaded ``(provider, adapter, case)`` fixture."""

    provider: str
    adapter: str
    case: str
    directory: Path
    source_path: Path
    content: bytes
    mime: str
    expected: Mapping[str, Any]

    @property
    def source_url(self) -> str:
        return str(self.expected["source_url"])

    @property
    def candidate_count(self) -> int:
        return int(self.expected["candidate_count"])

    @property
    def expected_candidates(self) -> Sequence[Mapping[str, Any]]:
        return tuple(self.expected.get("candidates", ()))

    @property
    def profile(self) -> str | None:
        """The extraction-profile name this case declares, if any."""

        declared = self.expected.get("profile")
        return str(declared) if declared is not None else None


# --- Loading ---------------------------------------------------------------


def case_directory(provider: str, adapter: str, case: str) -> Path:
    """Return the on-disk directory for one fixture case."""

    return FIXTURE_ROOT / provider / adapter / case


def available_cases(provider: str, adapter: str) -> tuple[str, ...]:
    """Return the case directories present for ``provider``/``adapter``.

    Vocabulary cases come first in :data:`CASES` order, then any other directory
    holding an ``expected.json`` -- a real provider's captured fixtures are
    conventionally named after the **source id** they serve (for example
    ``cloudflare-workers-limits``) rather than after a case, and those must be
    discovered too. Ordering is fully deterministic so a parametrised suite runs
    in the same order everywhere.
    """

    root = FIXTURE_ROOT / provider / adapter
    if not root.is_dir():
        return ()
    present = {p.name for p in root.iterdir() if p.is_dir() and (p / "expected.json").is_file()}
    vocabulary = tuple(case for case in CASES if case in present)
    other = tuple(sorted(present - set(vocabulary)))
    return vocabulary + other


def load_case(provider: str, adapter: str, case: str) -> FixtureCase:
    """Load one fixture case (``source.<ext>`` + ``expected.json``).

    The MIME is derived from the adapter's declared source type via the same
    :func:`~app.ingest.runner.fixture_mime_for` the production runner uses, so a
    test can never be served a content type the runner would not serve. When the
    fixture's ``expected.json`` declares a ``mime`` it must agree.
    """

    directory = case_directory(provider, adapter, case)
    if not directory.is_dir():
        raise FixtureCaseError(f"No fixture directory at {directory}.")

    extension = _ADAPTER_EXTENSION.get(adapter)
    if extension is None:
        raise FixtureCaseError(
            f"Unknown adapter directory '{adapter}'; known: {sorted(_ADAPTER_EXTENSION)}."
        )
    source_path = directory / f"source.{extension}"
    if not source_path.is_file():
        raise FixtureCaseError(f"Missing fixture document {source_path}.")

    expected_path = directory / "expected.json"
    if not expected_path.is_file():
        raise FixtureCaseError(f"Missing expectation file {expected_path}.")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    mime = fixture_mime_for(_ADAPTER_SOURCE_TYPE[adapter], extension)
    if mime is None:  # pragma: no cover - guarded by _ADAPTER_EXTENSION
        raise FixtureCaseError(f"No MIME mapping for adapter '{adapter}'.")
    declared = expected.get("mime")
    if declared is not None and declared != mime:
        raise FixtureCaseError(
            f"{expected_path}: declares mime '{declared}' but the runner would "
            f"serve '{mime}' for a '{adapter}' source."
        )

    return FixtureCase(
        provider=provider,
        adapter=adapter,
        case=case,
        directory=directory,
        source_path=source_path,
        content=source_path.read_bytes(),
        mime=mime,
        expected=expected,
    )


# --- Extraction ------------------------------------------------------------


def build_fixture_adapter(
    case: FixtureCase,
    *,
    official_domains: Sequence[str] | None = None,
    body: bytes | None = None,
    profile: str | None = None,
) -> SourceAdapter:
    """Build the adapter for ``case``, bound to an offline :class:`FixtureFetcher`.

    ``official_domains`` defaults to the host of the fixture's ``source_url``, so
    the fetch policy is exercised exactly as it would be in production (a URL
    outside the provider's allowlist is still refused).

    Profile-driven adapters (``html``, ``structured``) take their extraction
    profile name from ``profile`` or, failing that, from the fixture's
    ``expected.json`` ``profile`` key. Neither present is an error, never a
    default: guessing which table to read would fabricate facts.
    """

    url = case.source_url
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    policy = FetchPolicy(official_domains=tuple(official_domains or (host,)))
    fetcher = FixtureFetcher({url: (body if body is not None else case.content, case.mime)}, policy)

    if case.adapter == "rss":
        return RssFeedAdapter(fetcher, source_urls=(url,))
    if case.adapter == "reference-json":
        return JsonOfferAdapter(fetcher, source_urls=(url,))

    name = profile or case.profile
    if case.adapter in ("html", "structured") and name is None:
        raise FixtureCaseError(
            f"{case.directory / 'expected.json'}: a '{case.adapter}' case must declare "
            'its extraction profile (add a top-level "profile" key, or pass '
            "profile=... explicitly)."
        )
    if case.adapter == "html":
        return HtmlDocAdapter(fetcher, source_urls=(url,), profile=resolve_profile(name))
    if case.adapter == "structured":
        return StructuredApiAdapter(fetcher, source_urls=(url,), profile=resolve_json_profile(name))
    raise FixtureCaseError(
        f"No adapter builder for '{case.adapter}'; MCP sources are driven by their own client."
    )


def _normalise(value: Any) -> Any:
    """Tuples -> lists so adapter output compares equal to fixture JSON."""

    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    return value


def run_extraction_case(
    provider: str,
    adapter: str,
    case: str,
    *,
    official_domains: Sequence[str] | None = None,
    profile: str | None = None,
) -> tuple[FixtureCase, list[CandidateFacts]]:
    """Drive one extraction case and assert facts, evidence and hash stability.

    Asserts, for the loaded fixture:

    * the candidate count matches ``expected.json``;
    * every declared fact is extracted **verbatim** (a declared ``null`` must be
      extracted as UNKNOWN, never as a fabricated value);
    * the declared evidence URL/selector is produced and carries the document's
      content hash;
    * ``expect_valid`` agrees with :meth:`SourceAdapter.validate`;
    * extraction is deterministic -- a second pass over the same bytes yields an
      identical ``content_hash``.
    """

    fixture = load_case(provider, adapter, case)
    source_adapter = build_fixture_adapter(
        fixture, official_domains=official_domains, profile=profile
    )

    document = source_adapter.canonicalize(source_adapter.fetch(fixture.source_url))
    candidates = list(source_adapter.extract(document))
    assert len(candidates) == fixture.candidate_count, (
        f"{provider}/{adapter}/{case}: expected {fixture.candidate_count} "
        f"candidate(s), extracted {len(candidates)}."
    )

    for index, want in enumerate(fixture.expected_candidates):
        got = candidates[index]
        assert got.verification_state == want["verification_state"]
        assert got.verification_state != "verified", "extraction never verifies"
        for key, value in want.get("facts", {}).items():
            assert _normalise(got.facts.get(key)) == _normalise(value), (
                f"{provider}/{adapter}/{case}: fact '{key}' mismatch"
            )
        problems = list(source_adapter.validate(got))
        if want.get("expect_valid", True):
            assert problems == [], f"{provider}/{adapter}/{case}: unexpected {problems}"
        else:
            assert problems, f"{provider}/{adapter}/{case}: expected validation to flag this"
        if "evidence_url" in want:
            evidence = source_adapter.evidence(got)
            assert evidence, f"{provider}/{adapter}/{case}: no evidence location"
            assert evidence[0].url == want["evidence_url"]
            assert evidence[0].content_hash == document.content_hash
            if "evidence_selector" in want:
                assert evidence[0].selector == want["evidence_selector"]

    # Determinism: identical bytes always yield an identical content hash.
    again = list(
        build_fixture_adapter(fixture, official_domains=official_domains, profile=profile).extract(
            document
        )
    )
    assert [_content_hash(c.facts) for c in candidates] == [_content_hash(c.facts) for c in again]

    return fixture, candidates


def content_hashes(candidates: Sequence[CandidateFacts]) -> tuple[str, ...]:
    """The stable content hashes of ``candidates`` (order preserved)."""

    return tuple(_content_hash(c.facts) for c in candidates)


# --- Pipeline cases: withdrawn + stale (time-injected, real ORM rows) -------


@dataclass(frozen=True)
class WithdrawnOutcome:
    """What :func:`drive_withdrawn` observed."""

    event: ChangeEvent
    first_scan_id: int
    second_scan_id: int


@dataclass(frozen=True)
class StaleOutcome:
    """What :func:`drive_stale` observed."""

    staleness: StalenessAssessment
    candidates: tuple[Candidate, ...]
    withheld: int
    reviewed: int
    published: int
    review_items: tuple[ReviewItem, ...]
    now: datetime


def json_fetcher(url: str, payload: Mapping[str, Any], *, domains: Sequence[str]) -> FixtureFetcher:
    """A deterministic offline fetcher serving ``payload`` as JSON at ``url``."""

    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    return FixtureFetcher({url: (body, "application/json")}, FetchPolicy(official_domains=domains))


def offers_document(offers: Sequence[Mapping[str, Any]], *, provider: str = "example") -> dict:
    """Build a reference-JSON offers document from ``offers``."""

    return {"provider": provider, "offers": [dict(o) for o in offers]}


def drive_withdrawn(
    session: Session,
    source: Source,
    *,
    present: Sequence[Mapping[str, Any]],
    absent: Sequence[Mapping[str, Any]],
    domains: Sequence[str],
    now: datetime,
) -> WithdrawnOutcome:
    """Drive the ``withdrawn`` case end-to-end on real rows.

    Scan A observes ``present``; scan B observes ``absent`` (the same document
    with one offer row removed). Asserts that exactly one ``withdrawn``
    ``change_event`` is recorded for the vanished identity, that it is
    ``material`` (an offer disappearing always is), that it is still a *draft*
    (reconciliation has no publication path), and that it points back at the
    prior candidate with no new-side candidate.

    ``now`` is injected into both reconcile passes, so the outcome never depends
    on the wall clock and no ``sleep()`` is involved.
    """

    url = source.endpoint or ""

    first = run_scan(source, json_fetcher(url, offers_document(present), domains=domains), session)
    reconcile_scan(first, source, session, now=now)

    second = run_scan(source, json_fetcher(url, offers_document(absent), domains=domains), session)
    result = reconcile_scan(second, source, session, now=now)

    assert result.withdrawn == 1, f"expected exactly one withdrawal, got {result.withdrawn}"

    first_keys = {c.candidate_key for c in _candidates_of(session, first.id)}
    second_keys = {c.candidate_key for c in _candidates_of(session, second.id)}
    vanished = first_keys - second_keys
    assert len(vanished) == 1, f"expected one vanished identity, got {sorted(vanished)}"

    prior_ids = [c.id for c in _candidates_of(session, first.id) if c.candidate_key in vanished]
    events = list(
        session.execute(
            select(ChangeEvent).where(
                ChangeEvent.previous_candidate_id.in_(prior_ids),
                ChangeEvent.change_type == "withdrawn",
            )
        ).scalars()
    )
    assert len(events) == 1, f"expected one withdrawn event, got {len(events)}"
    event = events[0]
    assert event.materiality == "material"
    assert event.publication_status == "draft"
    assert event.new_candidate_id is None
    assert event.offer_id is None

    return WithdrawnOutcome(event=event, first_scan_id=first.id, second_scan_id=second.id)


def stale_clock(
    source: Source, fetched_at: datetime, *, margin: timedelta | None = None
) -> datetime:
    """Return a frozen ``now`` that is definitively beyond ``source``'s window.

    Derived from the source's own ``schedule`` (via
    :func:`~app.ingest.reconcile.parse_schedule_window`, which falls back to
    :data:`~app.ingest.reconcile.DEFAULT_STALENESS_WINDOW`) plus a margin, so the
    fixture stays correct if a schedule is retuned and it never depends on the
    real time of day.
    """

    window = parse_schedule_window(source.schedule, default=DEFAULT_STALENESS_WINDOW)
    return fetched_at + window + (margin or timedelta(days=1))


def drive_stale(
    session: Session,
    source: Source,
    publishing: Any,
    *,
    offers: Sequence[Mapping[str, Any]],
    domains: Sequence[str],
    now: datetime | None = None,
) -> StaleOutcome:
    """Drive the ``stale`` case end-to-end on real rows with a **frozen clock**.

    Scans ``offers``, then reconciles and runs the publication gate at a ``now``
    that is past the source's schedule window. Asserts that
    :func:`~app.ingest.reconcile.assess_staleness` reports ``stale``, that every
    candidate is flagged ``verification_state='stale'``, and that the gate
    **never publishes** -- the ``fresh`` hard condition has failed, so the
    candidate is either withheld or routed to human review (which raises a
    pending ``review_item``, unless the publisher already has an open review for
    the same identity, in which case it correctly declines to duplicate it).
    Which of the two depends only on confidence, and
    both are non-publication; what must never happen is a silent publish.

    ``now`` defaults to :func:`stale_clock` over the scan's own freshest
    snapshot, which makes the case reproducible regardless of when the suite
    runs.
    """

    from app.publish.publisher import publish_scan  # local: avoids an import cycle

    url = source.endpoint or ""
    scan = run_scan(source, json_fetcher(url, offers_document(offers), domains=domains), session)

    fetched_at = _freshest_snapshot_at(session, scan.id)
    frozen = now or stale_clock(source, fetched_at)

    staleness = assess_staleness(fetched_at, frozen, source.schedule)
    assert staleness.stale, (
        f"frozen clock {frozen.isoformat()} is not beyond the {staleness.window} window"
    )

    reconcile_result = reconcile_scan(scan, source, session, now=frozen)
    assert reconcile_result.stale_candidates >= 1

    candidates = _candidates_of(session, scan.id)
    assert candidates, "the stale case needs at least one candidate"
    assert all(c.verification_state == "stale" for c in candidates)

    publish_result = publish_scan(session, scan, source, publishing, now=frozen)
    assert publish_result.published == 0, "stale data must never be published"
    assert publish_result.unchanged == 0, "stale data must never take the publish route at all"
    assert publish_result.withheld + publish_result.reviewed >= 1, (
        "a stale candidate must be withheld or routed to review, not silently dropped"
    )
    assert all("fresh" in o.failed_conditions for o in publish_result.outcomes), (
        "the gate must record 'fresh' as the failed condition for stale data"
    )

    reviews = tuple(
        session.execute(select(ReviewItem).where(ReviewItem.scan_run_id == scan.id)).scalars()
    )
    if any(o.review_item_created for o in publish_result.outcomes):
        assert reviews, "a reviewed stale candidate must leave a pending review item"

    return StaleOutcome(
        staleness=staleness,
        candidates=tuple(candidates),
        withheld=publish_result.withheld,
        reviewed=publish_result.reviewed,
        published=publish_result.published,
        review_items=reviews,
        now=frozen,
    )


def _candidates_of(session: Session, scan_run_id: int) -> list[Candidate]:
    return list(
        session.execute(
            select(Candidate).where(Candidate.scan_run_id == scan_run_id).order_by(Candidate.id)
        ).scalars()
    )


def _freshest_snapshot_at(session: Session, scan_run_id: int) -> datetime:
    from app.models.domain import ScanRun, Snapshot

    source_id = session.execute(
        select(ScanRun.source_id).where(ScanRun.id == scan_run_id)
    ).scalar_one()
    fetched_at = session.execute(
        select(Snapshot.fetched_at)
        .where(Snapshot.source_id == source_id)
        .order_by(Snapshot.fetched_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if fetched_at is None:  # pragma: no cover - a scan always writes a snapshot
        raise FixtureCaseError("The scan produced no snapshot to age.")
    return fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=UTC)


__all__: Sequence[str] = (
    "CASES",
    "EXTRACTION_CASES",
    "PIPELINE_CASES",
    "FIXTURE_ROOT",
    "FixtureCase",
    "FixtureCaseError",
    "case_directory",
    "available_cases",
    "load_case",
    "build_fixture_adapter",
    "run_extraction_case",
    "content_hashes",
    "json_fetcher",
    "offers_document",
    "WithdrawnOutcome",
    "StaleOutcome",
    "drive_withdrawn",
    "drive_stale",
    "stale_clock",
)
