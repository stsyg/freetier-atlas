"""Offline AWS provider pipeline against a real PostgreSQL database.

Every byte here comes from a committed fixture through :class:`FixtureFetcher`;
no socket is opened. What the database adds is the part that cannot be proved
from one document: that six sources persist six independent candidates, that
NONE of them publishes an offer, and that a change to a published benefit
surfaces as a draft change event instead of overwriting the previous value.

The publication assertion is the point. AWS markets a perpetual Always Free
tier, and this slice extracts one -- but a payment method is required to open any
AWS account and usage above the perpetual allowance is charged, so the gate must
still withhold every AWS offer.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config import load_and_validate
from app.ingest.fetch import FetchPolicy, FixtureFetcher
from app.ingest.reconcile import reconcile_scan
from app.ingest.runner import build_fixture_fetcher, run_provider_scans
from app.ingest.scan import run_scan
from app.models.domain import (
    Candidate,
    ChangeEvent,
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    Quota,
    ReviewItem,
    Service,
    Snapshot,
    Source,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tests.support.fixtures import drive_stale, drive_withdrawn

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "aws.example.yaml"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ingest" / "aws" / "html"
DOMAINS = ("aws.amazon.com",)
SERVICES = (
    "AWS Free Tier",
    "AWS Free Tier free plan",
    "AWS 12 Month Free Tier",
    "Amazon DynamoDB",
    "Amazon API Gateway",
    "AWS Step Functions",
)

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start PostgreSQL and export it to enable.",
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


@skip_without_db
def test_runner_persists_six_official_review_candidates_and_zero_offers(
    session: Session, config
) -> None:
    snapshots_before = session.scalar(select(func.count()).select_from(Snapshot))
    result = _run(session, config)
    assert result.scanned == 6
    assert result.failed == 0
    # THE POINT OF THIS SLICE. AWS requires a payment method to open any account
    # and charges for usage above its allowances, so the publication gate must
    # withhold every one of these -- including the perpetual Step Functions tier.
    assert result.total_published == 0
    assert result.total_reviewed == 6

    provider = session.scalar(select(Provider).where(Provider.slug == "aws"))
    source_slugs = {source.id for source in config.sources}
    sources = list(
        session.scalars(
            select(Source).where(
                Source.provider_id == provider.id,
                Source.slug.in_(source_slugs),
            )
        )
    )
    run_ids = [outcome.scan_run_id for outcome in result.sources]
    candidates = list(session.scalars(select(Candidate).where(Candidate.scan_run_id.in_(run_ids))))
    evidence = list(
        session.scalars(
            select(Evidence).where(Evidence.candidate_id.in_([row.id for row in candidates]))
        )
    )
    reviews = list(
        session.scalars(
            select(ReviewItem).where(
                ReviewItem.admin_disposition == "pending",
                ReviewItem.candidate_facts["service"].as_string().in_(SERVICES),
            )
        )
    )

    assert len(sources) == 6
    assert len(candidates) == 6
    assert session.scalar(select(func.count()).select_from(Snapshot)) - snapshots_before == 6
    assert {row.candidate_facts["service"] for row in reviews} == set(SERVICES)
    assert {row.candidate_id for row in evidence} == {row.id for row in candidates}
    assert all(row.url.startswith("https://aws.amazon.com/") for row in evidence)
    assert all(row.admin_disposition == "pending" for row in reviews)

    # Six sources, six distinct documents. No AWS offer in this provider shares a
    # page with another, so none needs a section anchor to stay distinct.
    endpoints = {row.endpoint for row in sources}
    assert len(endpoints) == 6

    aws_offer_ids = select(Offer.id).join(Service).where(Service.provider_id == provider.id)
    assert (
        session.scalar(
            select(func.count())
            .select_from(Offer)
            .join(Service)
            .where(Service.provider_id == provider.id)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(OfferVersion)
            .where(OfferVersion.offer_id.in_(aws_offer_ids))
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(Quota)
            .join(OfferVersion, Quota.offer_version_id == OfferVersion.id)
            .where(OfferVersion.offer_id.in_(aws_offer_ids))
        )
        == 0
    )


@skip_without_db
def test_the_three_offer_kinds_persist_as_distinct_offers(session: Session, config) -> None:
    result = _run(session, config, publish=False)
    run_ids = [outcome.scan_run_id for outcome in result.sources]
    candidates = list(session.scalars(select(Candidate).where(Candidate.scan_run_id.in_(run_ids))))
    by_service = {row.candidate_facts["service"]: row.candidate_facts for row in candidates}

    assert by_service["AWS Free Tier"]["offer_type"] == "new_customer_credit"
    assert by_service["AWS Free Tier free plan"]["offer_type"] == "new_customer_credit"
    assert by_service["AWS 12 Month Free Tier"]["offer_type"] == "trial"
    assert by_service["Amazon API Gateway"]["offer_type"] == "trial"
    assert by_service["Amazon DynamoDB"]["offer_type"] == "recurring_quota"
    assert by_service["AWS Step Functions"]["offer_type"] == "always_free"

    # The credit belongs to the account-level plans and to nothing else.
    assert by_service["AWS Free Tier"]["credit_amount"] == "$200"
    assert "credit_amount" not in by_service["AWS Step Functions"]
    assert "credit_amount" not in by_service["Amazon API Gateway"]
    # The perpetual offer is still billed on overage.
    assert by_service["AWS Step Functions"]["exhaustion_behaviour"] == "automatic_billing"
    # The payment-method requirement is recorded where it is quoted, and nowhere
    # does an AWS candidate claim a card is NOT required.
    assert by_service["AWS Free Tier free plan"]["requires_card"] is True
    assert all(row.candidate_facts.get("requires_card") is not False for row in candidates)


@skip_without_db
def test_changed_case_creates_a_draft_change_event(session: Session, config) -> None:
    _run(session, config, publish=False)
    source = session.scalar(select(Source).where(Source.slug == "aws-free-tier-plan"))
    body = (FIXTURES / "changed" / "source.html").read_bytes()
    fetcher = FixtureFetcher(
        {source.endpoint: (body, "text/html")}, FetchPolicy(official_domains=DOMAINS)
    )
    scan = run_scan(source, fetcher, session)
    reconcile_scan(scan, source, session, now=datetime(2026, 8, 14, 20, 0, tzinfo=UTC))
    candidate_ids = list(
        session.scalars(select(Candidate.id).where(Candidate.scan_run_id == scan.id))
    )
    events = list(
        session.scalars(select(ChangeEvent).where(ChangeEvent.new_candidate_id.in_(candidate_ids)))
    )
    assert events
    assert any(row.change_type == "modified" for row in events)
    assert all(row.publication_status == "draft" for row in events)


@skip_without_db
def test_a_document_missing_its_pinned_block_persists_no_candidate(
    session: Session, config
) -> None:
    """The evidence floor, enforced at the pipeline level rather than in a unit test."""

    _run(session, config, publish=False)
    source = session.scalar(select(Source).where(Source.slug == "aws-free-tier-plan"))
    body = (FIXTURES / "partial" / "source.html").read_bytes()
    scan = run_scan(
        source,
        FixtureFetcher(
            {source.endpoint: (body, "text/html")}, FetchPolicy(official_domains=DOMAINS)
        ),
        session,
    )
    assert scan.status == "partial"
    assert scan.candidates_count == 0
    assert not list(session.scalars(select(Candidate).where(Candidate.scan_run_id == scan.id)))


@skip_without_db
def test_structurally_contradictory_document_is_rejected_not_reconciled(
    session: Session, config
) -> None:
    _run(session, config, publish=False)
    source = session.scalar(select(Source).where(Source.slug == "aws-free-tier-plan"))
    body = (FIXTURES / "contradictory" / "source.html").read_bytes()
    scan = run_scan(
        source,
        FixtureFetcher(
            {source.endpoint: (body, "text/html")},
            FetchPolicy(official_domains=DOMAINS),
        ),
        session,
    )
    assert scan.candidates_count == 0


_PIPELINE_OFFERS: tuple[Mapping[str, object], ...] = (
    {
        "service": "AWS Step Functions",
        "offer_type": "always_free",
        "requires_card": None,
        "has_paid_dependencies": None,
        "quotas": [
            {
                "metric": "free_state_transitions_per_month",
                "exhaustion_behaviour": "automatic_billing",
            }
        ],
    },
    {
        "service": "AWS Free Tier free plan",
        "offer_type": "new_customer_credit",
        "requires_card": True,
        "has_paid_dependencies": None,
        "quotas": [
            {"metric": "free_tier_credits", "exhaustion_behaviour": "manual_upgrade_required"}
        ],
    },
)


def _pipeline_source(session: Session) -> Source:
    source = Source(
        adapter_type="reference-json",
        trust_level="official",
        official=True,
        endpoint="https://aws.amazon.com/free/f008-pipeline-case",
        enabled=True,
        schedule="daily",
    )
    session.add(source)
    session.flush()
    return source


@skip_without_db
def test_withdrawn_case_is_a_material_draft_event(session: Session) -> None:
    outcome = drive_withdrawn(
        session,
        _pipeline_source(session),
        present=list(_PIPELINE_OFFERS),
        absent=[_PIPELINE_OFFERS[0]],
        domains=DOMAINS,
        now=datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
    )
    assert outcome.event.change_type == "withdrawn"
    assert outcome.event.materiality == "material"
    assert outcome.event.publication_status == "draft"


@skip_without_db
def test_stale_case_never_publishes(session: Session, config) -> None:
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
