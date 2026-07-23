"""Integration tests for the F004 Slice 6 quarantine/separation invariant.

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL (the stack smoke
scripts and CI drive this against the live compose Postgres). These prove the
*database* half of the two-layer separation guarantee installed by migration
0006 (the application half is covered offline by tests/unit/test_ingest_trust.py):

* a COMMUNITY scan quarantines rows in ``discovery_candidate`` and creates
  **zero** evidence / candidate-with-evidence / offer / offer_version;
* trigger ``trg_evidence_official_candidate`` rejects a raw INSERT of evidence
  that points at a non-official (community/quarantined) candidate;
* trigger ``trg_candidate_official_source`` rejects marking a candidate
  ``official`` when its source is not official, on both INSERT and UPDATE;
* an OFFICIAL scan still produces Candidate + Evidence (no regression: the
  triggers do not block the legitimate official pipeline);
* migration 0006 is reversible: up -> down -> up leaves both separation triggers
  present, the offer_version immutability trigger intact, and no ORM drift.

Data-mutating checks run inside a transaction that is rolled back; the
migration round-trip test restores ``head`` before returning.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.ingest.fetch import FetchPolicy, FixtureFetcher
from app.ingest.scan import run_scan
from app.models import alembic_include_object, metadata
from app.models.domain import (
    Candidate,
    DiscoveryCandidate,
    Evidence,
    Offer,
    OfferVersion,
    Snapshot,
    Source,
)
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
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

_OFFERS = {
    "provider": "example",
    "offers": [
        {
            "service": "Widgets",
            "offer_type": "always_free",
            "requires_card": False,
            "has_paid_dependencies": False,
            "quotas": [{"metric": "requests", "exhaustion_behaviour": "hard_stop"}],
        },
    ],
}


def _payload() -> bytes:
    return json.dumps(_OFFERS).encode("utf-8")


def _fetcher() -> FixtureFetcher:
    return FixtureFetcher({ENDPOINT: (_payload(), "application/json")}, POLICY)


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


@skip_without_db
def test_separation_triggers_installed(engine: Engine) -> None:
    with engine.connect() as conn:
        names = set(
            conn.execute(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgname IN "
                    "('trg_candidate_official_source', 'trg_evidence_official_candidate')"
                )
            ).scalars()
        )
    assert names == {
        "trg_candidate_official_source",
        "trg_evidence_official_candidate",
    }


@skip_without_db
def test_community_scan_quarantine_only_no_verified_artifacts(session: Session) -> None:
    """Community scan -> discovery_candidate only; 0 evidence/offer/offer_version."""

    source = _make_source(session, trust_level="community")

    offers_before = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    scan_run = run_scan(source, _fetcher(), session)

    # Quarantined rows exist.
    discoveries = list(
        session.execute(
            select(DiscoveryCandidate).where(DiscoveryCandidate.source_id == source.id)
        ).scalars()
    )
    assert len(discoveries) == 1
    assert all(d.verification_status == "unverified" for d in discoveries)

    # Candidates recorded, but flagged non-official.
    candidates = list(
        session.execute(select(Candidate).where(Candidate.scan_run_id == scan_run.id)).scalars()
    )
    assert candidates and all(c.official is False for c in candidates)

    # ZERO evidence for a community scan.
    candidate_ids = [c.id for c in candidates]
    evidence_count = session.execute(
        select(func.count()).select_from(Evidence).where(Evidence.candidate_id.in_(candidate_ids))
    ).scalar_one()
    assert evidence_count == 0

    # No publication artifacts of any kind.
    offers_after = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_after = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()
    assert offers_after == offers_before
    assert versions_after == versions_before


@skip_without_db
def test_db_rejects_evidence_for_community_candidate(session: Session) -> None:
    """Trigger B: raw INSERT of evidence for a non-official candidate is rejected."""

    source = _make_source(session, trust_level="community")
    scan_run = run_scan(source, _fetcher(), session)

    candidate = (
        session.execute(select(Candidate).where(Candidate.scan_run_id == scan_run.id))
        .scalars()
        .first()
    )
    assert candidate is not None and candidate.official is False

    snapshot_id = (
        session.execute(select(Snapshot.id).where(Snapshot.source_id == source.id))
        .scalars()
        .first()
    )
    assert snapshot_id is not None

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(
                text(
                    "INSERT INTO evidence "
                    "(source_id, candidate_id, snapshot_id, official, content_hash) "
                    "VALUES (:src, :cand, :snap, true, 'deadbeef')"
                ),
                {"src": source.id, "cand": candidate.id, "snap": snapshot_id},
            )


@skip_without_db
def test_db_rejects_official_candidate_on_community_source_insert(session: Session) -> None:
    """Trigger A: INSERT of an official candidate on a community source is rejected."""

    source = _make_source(session, trust_level="community")
    scan_run = run_scan(source, _fetcher(), session)

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(
                text(
                    "INSERT INTO candidate "
                    "(scan_run_id, source_id, verification_state, candidate_facts, "
                    " candidate_key, content_hash, official) "
                    "VALUES (:run, :src, 'candidate', '{}'::jsonb, 'k', 'h', true)"
                ),
                {"run": scan_run.id, "src": source.id},
            )


@skip_without_db
def test_db_rejects_promoting_community_candidate_to_official(session: Session) -> None:
    """Trigger A: UPDATE flipping a community candidate to official is rejected.

    This is the crucial 'no automated promotion from community data' guarantee:
    a quarantined/community candidate cannot be relabelled official in place.
    """

    source = _make_source(session, trust_level="community")
    scan_run = run_scan(source, _fetcher(), session)
    candidate = (
        session.execute(select(Candidate).where(Candidate.scan_run_id == scan_run.id))
        .scalars()
        .first()
    )
    assert candidate is not None and candidate.official is False

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(
                text("UPDATE candidate SET official = true WHERE id = :cid"),
                {"cid": candidate.id},
            )


@skip_without_db
def test_official_pipeline_unregressed(session: Session) -> None:
    """Official scan still yields Candidate + Evidence: triggers do not block it."""

    source = _make_source(session, trust_level="official")
    scan_run = run_scan(source, _fetcher(), session)

    candidates = list(
        session.execute(select(Candidate).where(Candidate.scan_run_id == scan_run.id)).scalars()
    )
    assert candidates and all(c.official is True for c in candidates)

    candidate_ids = [c.id for c in candidates]
    evidence = list(
        session.execute(select(Evidence).where(Evidence.candidate_id.in_(candidate_ids))).scalars()
    )
    assert len(evidence) == len(candidates)
    assert all(e.official is True for e in evidence)


@skip_without_db
def test_migration_0006_round_trip(engine: Engine) -> None:
    """0006 up -> down -> up: separation triggers toggle, others survive, no drift."""

    cfg = _alembic_config()

    sep_trigger_sql = text(
        "SELECT count(*) FROM pg_trigger WHERE tgname IN "
        "('trg_candidate_official_source', 'trg_evidence_official_candidate')"
    )
    immutable_sql = text(
        "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_offer_version_immutable'"
    )

    # Downgrade removes exactly the two separation triggers.
    command.downgrade(cfg, "0005_change_event_candidate_link")
    with engine.connect() as conn:
        assert conn.execute(sep_trigger_sql).scalar_one() == 0
        # The unrelated immutability trigger must survive the 0006 downgrade.
        assert conn.execute(immutable_sql).scalar_one() == 1

    # Re-upgrade reinstalls them.
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert conn.execute(sep_trigger_sql).scalar_one() == 2
        assert conn.execute(immutable_sql).scalar_one() == 1

    # Domain tables still present and no model/migration drift after the round trip.
    existing = set(inspect(engine).get_table_names())
    assert set(metadata.tables.keys()) <= existing

    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "target_metadata": metadata,
                "include_object": alembic_include_object,
                "compare_type": True,
            },
        )
        assert compare_metadata(ctx, metadata) == [], "drift after 0006 round trip"
