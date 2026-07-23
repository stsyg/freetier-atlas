"""ScanRun orchestration: fetch -> canonicalize -> extract -> validate -> persist.

:func:`run_scan` drives one source through its adapter and persists the
*pre-publication* results of the ingestion pipeline (docs/ARCHITECTURE.md ->
"Ingestion pipeline"):

* a :class:`~app.models.domain.ScanRun` row that accounts for the run
  (documents / candidates / changes / errors);
* one :class:`~app.models.domain.Snapshot` per fetched document (hashed);
* one :class:`~app.models.domain.Candidate` per extracted, valid candidate fact,
  in a pre-publication ``verification_state`` and carrying a deterministic
  ``content_hash`` (of its facts) plus a stable ``candidate_key`` (its identity);
* for **official** sources only, one :class:`~app.models.domain.Evidence` row per
  candidate linking the Source + Snapshot to the Candidate;
* for **community** sources, one quarantined
  :class:`~app.models.domain.DiscoveryCandidate` per candidate and **never** any
  ``evidence`` row.

Hard invariants (docs/SOURCE_REUSE_AND_PROVENANCE.md, docs/SECURITY_PRIVACY_ABUSE.md):

* There is **no publication path** here. ``run_scan`` never creates or mutates
  ``offer`` / ``offer_version`` and never writes ``change_event`` rows; the
  ``changes_count`` is only a per-run tally used for observability.
* Only ``trust_level == "official"`` sources may create ``evidence``.
* The network is reached solely through the injected
  :class:`~app.ingest.fetch.Fetcher` (the Slice 1 seam); this module opens no
  socket of its own.

Determinism / idempotency: the content hash is computed over a canonical JSON
serialisation of the extracted facts, so re-scanning byte-identical input yields
identical candidate hashes, detects zero changes, and produces zero spurious
change events.

The caller owns the transaction: ``run_scan`` uses ``session.flush()`` (so newly
written rows are visible to in-run change detection) but never commits or rolls
back.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.adapters import HtmlDocAdapter, RssFeedAdapter, resolve_profile
from app.ingest.base import CandidateFacts, SourceAdapter, SourceDocument
from app.ingest.fetch import Fetcher, FetchError
from app.ingest.reference import JsonOfferAdapter
from app.models.domain import (
    Candidate,
    DiscoveryCandidate,
    Evidence,
    ScanRun,
    Snapshot,
    Source,
)

#: Adapter factories keyed by ``source.adapter_type``. Slice 2 wires only the
#: reference JSON adapter; further adapters register here without touching the
#: orchestration.
AdapterFactory = Callable[[Source, Fetcher], SourceAdapter]

ADAPTER_REGISTRY: dict[str, AdapterFactory] = {
    "reference-json": lambda source, fetcher: JsonOfferAdapter(
        fetcher, source_urls=_source_urls(source)
    ),
    "rss": lambda source, fetcher: RssFeedAdapter(fetcher, source_urls=_source_urls(source)),
    "html": lambda source, fetcher: HtmlDocAdapter(
        fetcher,
        source_urls=_source_urls(source),
        profile=resolve_profile(source.parser_profile),
    ),
}


class UnknownAdapterError(ValueError):
    """Raised when ``source.adapter_type`` has no registered factory."""


def _source_urls(source: Source) -> tuple[str, ...]:
    return (source.endpoint,) if source.endpoint else ()


def build_adapter(source: Source, fetcher: Fetcher) -> SourceAdapter:
    """Construct the adapter for ``source`` bound to the injected ``fetcher``."""

    try:
        factory = ADAPTER_REGISTRY[source.adapter_type]
    except KeyError as exc:
        raise UnknownAdapterError(
            f"No adapter registered for adapter_type '{source.adapter_type}'; "
            f"known: {sorted(ADAPTER_REGISTRY)}."
        ) from exc
    return factory(source, fetcher)


def _json_safe(value: Any) -> Any:
    """Recursively convert a value into a JSON-serialisable, order-stable form.

    Tuples become lists and mapping keys are coerced to strings so the same facts
    always serialise to the same canonical text (JSONB round-trips lists, not
    tuples).
    """

    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _canonical_json(value: Any) -> str:
    """Serialise ``value`` deterministically (sorted keys, no whitespace)."""

    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"))


def _content_hash(facts: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical facts -- identical facts hash identically."""

    return hashlib.sha256(_canonical_json(facts).encode("utf-8")).hexdigest()


def _candidate_key(candidate: CandidateFacts) -> str:
    """Stable identity hash used to correlate a candidate across scans."""

    identity = {
        "provider": candidate.provider,
        "source_url": candidate.source_url,
        "service": candidate.facts.get("service"),
        "offer_type": candidate.facts.get("offer_type"),
    }
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


def _latest_prior_hash(
    session: Session, *, source_id: int, candidate_key: str, exclude_scan_run_id: int
) -> str | None:
    """Return the most recent prior candidate's ``content_hash``, if any.

    Scoped to the same source + identity and excluding the current run, so change
    detection compares this scan against the last time the same candidate was seen.
    """

    stmt = (
        select(Candidate.content_hash)
        .where(
            Candidate.source_id == source_id,
            Candidate.candidate_key == candidate_key,
            Candidate.scan_run_id != exclude_scan_run_id,
        )
        .order_by(Candidate.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def run_scan(source: Source, fetcher: Fetcher, session: Session) -> ScanRun:
    """Run one ingestion scan for ``source`` and persist its results.

    Returns the flushed :class:`ScanRun`. The caller is responsible for
    committing (or rolling back) the surrounding transaction.
    """

    adapter = build_adapter(source, fetcher)
    is_official = source.trust_level == "official"

    scan_run = ScanRun(source_id=source.id, status="running")
    session.add(scan_run)
    session.flush()

    documents = 0
    candidates = 0
    changes = 0
    errors = 0

    for url in adapter.discover():
        try:
            result = adapter.fetch(url)
        except FetchError:
            errors += 1
            continue

        documents += 1
        snapshot = Snapshot(
            source_id=source.id,
            content_location=result.final_url,
            mime_type=result.mime,
            content_hash=result.content_hash,
            fetched_at=result.fetched_at,
        )
        session.add(snapshot)
        session.flush()

        document: SourceDocument = adapter.canonicalize(result)

        for candidate_facts in adapter.extract(document):
            problems = adapter.validate(candidate_facts)
            if problems:
                errors += 1
                continue

            facts = _json_safe(candidate_facts.facts)
            content_hash = _content_hash(candidate_facts.facts)
            key = _candidate_key(candidate_facts)

            prior_hash = _latest_prior_hash(
                session,
                source_id=source.id,
                candidate_key=key,
                exclude_scan_run_id=scan_run.id,
            )
            if prior_hash is None or prior_hash != content_hash:
                changes += 1

            candidate = Candidate(
                scan_run_id=scan_run.id,
                source_id=source.id,
                provider=candidate_facts.provider,
                source_url=candidate_facts.source_url,
                verification_state=candidate_facts.verification_state,
                candidate_facts=facts,
                candidate_key=key,
                content_hash=content_hash,
                official=is_official,
            )
            session.add(candidate)
            session.flush()
            candidates += 1

            if is_official:
                _persist_evidence(
                    session,
                    adapter=adapter,
                    candidate_facts=candidate_facts,
                    candidate=candidate,
                    document=document,
                    snapshot=snapshot,
                    source=source,
                )
            else:
                _persist_discovery_candidate(
                    session,
                    candidate_facts=candidate_facts,
                    facts=facts,
                    source=source,
                )

    scan_run.documents_count = documents
    scan_run.candidates_count = candidates
    scan_run.changes_count = changes
    scan_run.errors_count = errors
    scan_run.status = _scan_status(documents=documents, errors=errors)
    scan_run.finished_at = datetime.now(UTC)

    source.health = _source_health(documents=documents, errors=errors)

    session.flush()
    return scan_run


def _persist_evidence(
    session: Session,
    *,
    adapter: SourceAdapter,
    candidate_facts: CandidateFacts,
    candidate: Candidate,
    document: SourceDocument,
    snapshot: Snapshot,
    source: Source,
) -> None:
    """Link Source + Snapshot to a Candidate as official provenance evidence."""

    for location in adapter.evidence(candidate_facts):
        session.add(
            Evidence(
                source_id=source.id,
                snapshot_id=snapshot.id,
                candidate_id=candidate.id,
                offer_version_id=None,
                official=True,
                url=location.url,
                selector=location.selector,
                excerpt=location.excerpt,
                content_hash=location.content_hash or document.content_hash,
            )
        )


def _persist_discovery_candidate(
    session: Session,
    *,
    candidate_facts: CandidateFacts,
    facts: Mapping[str, Any],
    source: Source,
) -> None:
    """Quarantine a community-derived candidate; never creates evidence."""

    service = facts.get("service")
    session.add(
        DiscoveryCandidate(
            source_id=source.id,
            repository=source.endpoint or candidate_facts.provider,
            url=candidate_facts.source_url,
            licence=None,
            import_method="automated",
            verification_status="unverified",
            candidate_name=str(service) if service is not None else None,
            official_url=None,
            notes=f"Discovered via scan of source {source.id}.",
        )
    )


def _scan_status(*, documents: int, errors: int) -> str:
    if errors == 0:
        return "success"
    if documents > 0:
        return "partial"
    return "failed"


def _source_health(*, documents: int, errors: int) -> str:
    if documents == 0:
        return "unhealthy"
    if errors == 0:
        return "healthy"
    return "degraded"


__all__: Sequence[str] = (
    "run_scan",
    "build_adapter",
    "ADAPTER_REGISTRY",
    "UnknownAdapterError",
)
