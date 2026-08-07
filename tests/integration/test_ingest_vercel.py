"""Real-PostgreSQL, offline integration proof for the Vercel slice (F008 P2)."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config import load_and_validate
from app.ingest.runner import build_fixture_fetcher, run_provider_scans
from app.models.domain import (
    Candidate,
    ChangeEvent,
    Evidence,
    Offer,
    OfferVersion,
    Quota,
    ReviewItem,
    ScanRun,
    Snapshot,
    Source,
)
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tests.support.fixtures import drive_stale, drive_withdrawn

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "vercel.example.yaml"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ingest" / "vercel" / "html"
DOMAINS = ("vercel.com",)
PIPELINE_ENDPOINT = "https://vercel.com/docs/f008-vercel-pipeline-case"
Z0_SERVICES = frozenset(
    {
        "Vercel App Hosting",
        "Vercel Functions",
        "Vercel Edge Network",
        "Vercel Built-in CI/CD",
        "Vercel Monitoring and Logs",
        "Vercel Global Config",
        "Vercel Blob",
    }
)

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres and export it to enable.",
)


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
        _reclaim_dead_space(eng)
        eng.dispose()


_BLOATED_TABLES = (
    "change_event",
    "review_item",
    "quota",
    "offer_version",
    "offer",
    "evidence",
    "candidate",
    "snapshot",
    "scan_run",
    "source",
)


def _reclaim_dead_space(eng: Engine) -> None:
    """Rewrite emptied ingest heaps so reconcile's adversarial ctid guard stays real."""

    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for table in _BLOATED_TABLES:
            conn.execute(text(f"VACUUM (FULL) {table}"))


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


@pytest.fixture(scope="module")
def config():
    return load_and_validate(CONFIG_PATH)


def _run(session: Session, config, *, publish: bool = True):
    return run_provider_scans(
        session,
        config,
        build_fixture_fetcher(config, FIXTURES),
        reconcile=True,
        publish=publish,
    )


def _source(session: Session, suffix: str) -> Source:
    return session.execute(select(Source).where(Source.endpoint.like(f"%{suffix}"))).scalar_one()


@skip_without_db
def test_runner_persists_official_scans_candidates_and_evidence(session: Session, config) -> None:
    snapshots_before = session.scalar(select(func.count()).select_from(Snapshot))
    result = _run(session, config)
    scanned = [outcome for outcome in result.sources if outcome.status == "scanned"]
    assert len(scanned) == 6
    scan_ids = [outcome.scan_run_id for outcome in scanned if outcome.scan_run_id]
    assert len(scan_ids) == 6
    runs = list(session.scalars(select(ScanRun).where(ScanRun.id.in_(scan_ids))))
    candidates = list(session.scalars(select(Candidate).where(Candidate.scan_run_id.in_(scan_ids))))
    evidence = list(
        session.scalars(
            select(Evidence).where(Evidence.candidate_id.in_([row.id for row in candidates]))
        )
    )
    assert len(runs) == 6
    assert session.scalar(select(func.count()).select_from(Snapshot)) - snapshots_before == 6
    assert len(candidates) == 10
    assert len(evidence) == 10
    assert all(row.url.startswith("https://vercel.com/") for row in evidence)
    hobby_candidates = [
        row for row in candidates if row.candidate_facts.get("service") in Z0_SERVICES
    ]
    assert all(
        row.candidate_facts.get("notes") == "Personal, non-commercial use only"
        for row in hobby_candidates
        if row.candidate_facts.get("service") != "Vercel Blob"
    )
    storage_source = _source(session, "/docs/storage")
    storage = next(run for run in runs if run.source_id == storage_source.id)
    assert storage.status == "partial"
    assert storage.errors_count == 1


@skip_without_db
def test_gate_publishes_traceable_versions_and_withholds_incomplete_rows(
    session: Session, config
) -> None:
    _run(session, config)
    offers = {
        offer.service.canonical_name: offer
        for offer in session.scalars(select(Offer).join(Offer.service))
        if offer.service.canonical_name
        in Z0_SERVICES
        | {
            "Vercel Pro trial",
            "Vercel Queues",
            "Ling 3.0 Tiny via Vercel AI Gateway",
        }
    }
    assert Z0_SERVICES <= set(offers)
    assert "Vercel Pro trial" in offers
    assert "Vercel Queues" in offers
    assert "Ling 3.0 Tiny via Vercel AI Gateway" not in offers
    for name, offer in offers.items():
        versions = list(
            session.scalars(select(OfferVersion).where(OfferVersion.offer_id == offer.id))
        )
        quotas = list(
            session.scalars(
                select(Quota).where(
                    Quota.offer_version_id.in_([version.id for version in versions])
                )
            )
        )
        assert versions, name
        assert quotas, name
        assert all(quota.metric for quota in quotas)
        assert any(quota.amount is not None for quota in quotas)


@skip_without_db
def test_persisted_z0_and_non_z0_verdicts_match_official_conditions(
    session: Session, config
) -> None:
    _run(session, config)
    rows = list(
        session.execute(select(OfferVersion, Offer).join(OfferVersion.offer).join(Offer.service))
    )
    verdicts = {offer.service.canonical_name: version.zero_cost_class for version, offer in rows}
    assert all(verdicts[name] == "Z0_TRUE_FREE" for name in Z0_SERVICES)
    assert verdicts["Vercel Pro trial"] == "Z2_TEMPORARY_OR_CONDITIONAL"
    assert verdicts["Vercel Queues"] == "UNKNOWN"


def _scan_with_fixture(session: Session, source: Source, case: str, *, now: datetime) -> ScanRun:
    from app.ingest.fetch import FetchPolicy, FixtureFetcher
    from app.ingest.reconcile import reconcile_scan
    from app.ingest.scan import run_scan

    body = (FIXTURES / case / "source.html").read_bytes()
    scan = run_scan(
        source,
        FixtureFetcher(
            {source.endpoint: (body, "text/html")},
            FetchPolicy(official_domains=DOMAINS),
        ),
        session,
    )
    reconcile_scan(scan, source, session, now=now)
    return scan


@skip_without_db
def test_changed_allowance_persists_a_modified_change_event(session: Session, config) -> None:
    _run(session, config, publish=False)
    scan = _scan_with_fixture(
        session,
        _source(session, "/docs/plans/hobby"),
        "changed",
        now=datetime(2026, 8, 7, 12, 0, tzinfo=UTC),
    )
    candidate_ids = list(
        session.scalars(select(Candidate.id).where(Candidate.scan_run_id == scan.id))
    )
    events = list(
        session.scalars(select(ChangeEvent).where(ChangeEvent.new_candidate_id.in_(candidate_ids)))
    )
    assert events
    assert any(event.change_type == "modified" for event in events)
    assert all(event.materiality != "non_material" for event in events)
    assert all(event.publication_status == "draft" for event in events)


@pytest.mark.parametrize("case", ["partial", "malformed"])
@skip_without_db
def test_invalid_documents_persist_failure_state_without_candidates_or_versions(
    session: Session, config, case: str
) -> None:
    from app.publish.publisher import publish_scan

    _run(session, config, publish=False)
    source = _source(session, "/docs/plans/hobby")
    before_versions = session.scalar(select(func.count()).select_from(OfferVersion))
    before_snapshots = session.scalar(select(func.count()).select_from(Snapshot))
    scan = _scan_with_fixture(session, source, case, now=datetime(2026, 8, 7, 12, 0, tzinfo=UTC))
    assert scan.status == "partial"
    assert scan.errors_count == 1
    assert scan.candidates_count == 0
    assert session.scalar(select(func.count()).select_from(Snapshot)) == before_snapshots + 1
    outcome = publish_scan(
        session, scan, source, config.publishing, now=datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    )
    assert outcome.published == 0
    assert session.scalar(select(func.count()).select_from(OfferVersion)) == before_versions


@skip_without_db
def test_cross_source_contradiction_is_withheld_and_queued(session: Session, config) -> None:
    from app.ingest.fetch import FetchPolicy, FixtureFetcher
    from app.ingest.reconcile import reconcile_scan
    from app.ingest.scan import run_scan
    from app.publish.publisher import publish_scan

    _run(session, config, publish=False)
    original = _source(session, "/docs/plans/pro-plan/trials")
    conflicting = Source(
        provider_id=original.provider_id,
        slug="vercel-pro-trial-conflicting",
        adapter_type=original.adapter_type,
        endpoint="https://vercel.com/docs/plans/pro-plan/trials-conflict",
        parser_profile=original.parser_profile,
        schedule=original.schedule,
        trust_level=original.trust_level,
        official=True,
    )
    session.add(conflicting)
    session.flush()
    body = (FIXTURES / "contradictory" / "source.html").read_bytes()
    now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    scan = run_scan(
        conflicting,
        FixtureFetcher(
            {conflicting.endpoint: (body, "text/html")},
            FetchPolicy(official_domains=DOMAINS),
        ),
        session,
    )
    result = reconcile_scan(scan, conflicting, session, now=now)
    reviews = list(session.scalars(select(ReviewItem).where(ReviewItem.scan_run_id == scan.id)))
    assert result.review_items >= 1
    assert reviews
    assert all(review.admin_disposition == "pending" for review in reviews)
    fields = {
        item["field"]
        for review in reviews
        for item in (review.evidence_conflict or {}).get("conflicts", [])
    }
    assert "requires_card" in fields
    versions_before = session.scalar(select(func.count()).select_from(OfferVersion))
    outcome = publish_scan(session, scan, conflicting, config.publishing, now=now)
    assert all(item.decision != "publish" for item in outcome.outcomes)
    assert session.scalar(select(func.count()).select_from(OfferVersion)) == versions_before


_PIPELINE_OFFERS: tuple[Mapping[str, object], ...] = (
    {
        "service": "Vercel Functions",
        "offer_type": "always_free",
        "requires_card": False,
        "has_paid_dependencies": False,
        "quotas": [{"metric": "invocations", "exhaustion_behaviour": "hard_stop"}],
    },
    {
        "service": "Vercel Blob",
        "offer_type": "always_free",
        "requires_card": False,
        "has_paid_dependencies": False,
        "quotas": [{"metric": "storage", "exhaustion_behaviour": "hard_stop"}],
    },
)


def _pipeline_source(session: Session) -> Source:
    source = Source(
        adapter_type="reference-json",
        trust_level="official",
        official=True,
        endpoint=PIPELINE_ENDPOINT,
        enabled=True,
        schedule="daily",
    )
    session.add(source)
    session.flush()
    return source


@skip_without_db
def test_withdrawn_offer_is_a_material_persisted_event(session: Session) -> None:
    outcome = drive_withdrawn(
        session,
        _pipeline_source(session),
        present=list(_PIPELINE_OFFERS),
        absent=[_PIPELINE_OFFERS[0]],
        domains=DOMAINS,
        now=datetime(2026, 8, 7, 12, 0, tzinfo=UTC),
    )
    assert outcome.event.change_type == "withdrawn"
    assert outcome.event.materiality == "material"
    assert outcome.event.new_candidate_id is None
    assert outcome.event.publication_status == "draft"


@skip_without_db
def test_stale_evidence_never_publishes(session: Session, config) -> None:
    outcome = drive_stale(
        session,
        _pipeline_source(session),
        config.publishing,
        offers=[_PIPELINE_OFFERS[0]],
        domains=DOMAINS,
    )
    assert outcome.staleness.stale
    assert outcome.published == 0
    assert outcome.withheld + outcome.reviewed >= 1
