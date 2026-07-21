"""Integration tests for ScanRun orchestration (F004 Slice 2, migration 0004).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL (the stack smoke
scripts and CI drive this against the live compose Postgres). Exercises the
end-to-end ingestion persistence path through :func:`app.ingest.scan.run_scan`
against a real database:

* an OFFICIAL-source scan produces a ScanRun with correct
  documents/candidates/changes/errors counts, a hashed Snapshot, pre-publication
  Candidate rows, and Evidence linked to Source + Snapshot + Candidate;
* re-scanning byte-identical input is reproducible: identical candidate hashes,
  zero detected changes, and zero change_event rows;
* no ``offer`` / ``offer_version`` row is ever created (no publication path);
* a COMMUNITY-source scan quarantines rows in ``discovery_candidate`` and creates
  **no** ``evidence``.

Every test runs inside a transaction that is rolled back, leaving the schema and
data clean.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ingest.fetch import FetchPolicy, FixtureFetcher, content_hash
from app.ingest.scan import run_scan
from app.models.domain import (
    Candidate,
    ChangeEvent,
    DiscoveryCandidate,
    Evidence,
    Offer,
    OfferVersion,
    Snapshot,
    Source,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

ENDPOINT = "https://provider.example/offers.json"
POLICY = FetchPolicy(official_domains=("provider.example",))

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)

_TWO_OFFERS = {
    "provider": "example",
    "offers": [
        {
            "service": "Widgets",
            "offer_type": "always_free",
            "requires_card": False,
            "has_paid_dependencies": False,
            "quotas": [{"metric": "requests", "exhaustion_behaviour": "hard_stop"}],
        },
        {
            "service": "Gadgets",
            "offer_type": "trial",
            "requires_card": True,
            "has_paid_dependencies": False,
            "quotas": [{"metric": "builds", "exhaustion_behaviour": "throttled"}],
        },
    ],
}

_ONE_VALID_ONE_INVALID = {
    "provider": "example",
    "offers": [
        {
            "service": "Widgets",
            "offer_type": "always_free",
            "requires_card": False,
            "has_paid_dependencies": False,
            "quotas": [],
        },
        # Missing the required 'service' field -> validation problem.
        {"offer_type": "trial", "quotas": []},
    ],
}


def _payload(document: dict) -> bytes:
    return json.dumps(document).encode("utf-8")


def _fetcher(document: dict) -> FixtureFetcher:
    return FixtureFetcher({ENDPOINT: (_payload(document), "application/json")}, POLICY)


def _alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return cfg


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    command.upgrade(_alembic_config(), "head")
    eng = create_engine(DATABASE_URL)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session bound to a transaction that is always rolled back."""

    conn = engine.connect()
    trans = conn.begin()
    sess = Session(bind=conn)
    try:
        yield sess
    finally:
        sess.close()
        trans.rollback()
        conn.close()


def _make_source(session: Session, *, trust_level: str) -> Source:
    source = Source(
        adapter_type="reference-json",
        trust_level=trust_level,
        official=trust_level == "official",
        endpoint=ENDPOINT,
        enabled=True,
    )
    session.add(source)
    session.flush()
    return source


def _candidates_for(session: Session, scan_run_id: int) -> list[Candidate]:
    return list(
        session.execute(select(Candidate).where(Candidate.scan_run_id == scan_run_id)).scalars()
    )


@skip_without_db
def test_official_scan_persists_run_snapshot_candidates_evidence(
    session: Session,
) -> None:
    source = _make_source(session, trust_level="official")

    scan_run = run_scan(source, _fetcher(_TWO_OFFERS), session)

    # ScanRun accounting.
    assert scan_run.documents_count == 1
    assert scan_run.candidates_count == 2
    assert scan_run.errors_count == 0
    assert scan_run.changes_count == 2  # first scan -> both candidates are new
    assert scan_run.status == "success"
    assert scan_run.finished_at is not None

    # Exactly one hashed Snapshot, hash == sha256 of the fetched bytes.
    snapshots = list(
        session.execute(select(Snapshot).where(Snapshot.source_id == source.id)).scalars()
    )
    assert len(snapshots) == 1
    assert snapshots[0].content_hash == content_hash(_payload(_TWO_OFFERS))

    # Candidates persisted in a pre-publication state, flagged official.
    candidates = _candidates_for(session, scan_run.id)
    assert len(candidates) == 2
    for candidate in candidates:
        assert candidate.verification_state == "candidate"
        assert candidate.official is True
        assert candidate.content_hash  # deterministic, non-empty
        assert candidate.candidate_key
        # A candidate never resolves directly to a published offer version.
        assert candidate.offer_id is None

    # Evidence links Source + Snapshot + Candidate; never an offer_version.
    candidate_ids = [c.id for c in candidates]
    evidence = list(
        session.execute(select(Evidence).where(Evidence.candidate_id.in_(candidate_ids))).scalars()
    )
    assert len(evidence) == 2
    for row in evidence:
        assert row.source_id == source.id
        assert row.snapshot_id == snapshots[0].id
        assert row.candidate_id in candidate_ids
        assert row.official is True
        assert row.offer_version_id is None

    # Source health reflects a clean run.
    assert source.health == "healthy"


@skip_without_db
def test_rescan_identical_input_is_reproducible(session: Session) -> None:
    source = _make_source(session, trust_level="official")

    change_events_before = session.execute(
        select(func.count()).select_from(ChangeEvent)
    ).scalar_one()

    first = run_scan(source, _fetcher(_TWO_OFFERS), session)
    second = run_scan(source, _fetcher(_TWO_OFFERS), session)

    first_hashes = {c.candidate_key: c.content_hash for c in _candidates_for(session, first.id)}
    second_hashes = {c.candidate_key: c.content_hash for c in _candidates_for(session, second.id)}

    # Byte-identical input -> identical candidate hashes across scans.
    assert first_hashes == second_hashes
    assert first.changes_count == 2  # first observation of each candidate
    assert second.changes_count == 0  # nothing changed on the re-scan

    # No spurious change_event rows are ever written by a scan.
    change_events_after = session.execute(
        select(func.count()).select_from(ChangeEvent)
    ).scalar_one()
    assert change_events_after == change_events_before


@skip_without_db
def test_scan_never_writes_offer_or_offer_version(session: Session) -> None:
    source = _make_source(session, trust_level="official")

    offers_before = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    run_scan(source, _fetcher(_TWO_OFFERS), session)

    offers_after = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_after = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    assert offers_after == offers_before
    assert versions_after == versions_before


@skip_without_db
def test_community_scan_quarantines_and_creates_no_evidence(
    session: Session,
) -> None:
    source = _make_source(session, trust_level="community")

    scan_run = run_scan(source, _fetcher(_TWO_OFFERS), session)

    assert scan_run.candidates_count == 2

    # Candidates are recorded but flagged non-official.
    candidates = _candidates_for(session, scan_run.id)
    assert candidates and all(c.official is False for c in candidates)

    # Community provenance is quarantined in discovery_candidate.
    discoveries = list(
        session.execute(
            select(DiscoveryCandidate).where(DiscoveryCandidate.source_id == source.id)
        ).scalars()
    )
    assert len(discoveries) == 2
    for discovery in discoveries:
        assert discovery.verification_status == "unverified"
        assert discovery.import_method == "automated"
        assert discovery.repository == ENDPOINT

    # A community source creates NO evidence rows whatsoever.
    candidate_ids = [c.id for c in candidates]
    evidence_count = session.execute(
        select(func.count()).select_from(Evidence).where(Evidence.candidate_id.in_(candidate_ids))
    ).scalar_one()
    assert evidence_count == 0


@skip_without_db
def test_invalid_candidate_is_counted_as_error_and_skipped(
    session: Session,
) -> None:
    source = _make_source(session, trust_level="official")

    scan_run = run_scan(source, _fetcher(_ONE_VALID_ONE_INVALID), session)

    assert scan_run.documents_count == 1
    assert scan_run.candidates_count == 1  # the invalid offer is skipped
    assert scan_run.errors_count == 1
    assert scan_run.status == "partial"
    assert source.health == "degraded"
