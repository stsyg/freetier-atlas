"""Real-PostgreSQL proof for same-document matrix and assertion extraction."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config.models import PublishingSection
from app.ingest import FetchPolicy, FixtureFetcher, run_scan
from app.models.domain import (
    Candidate,
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    Quota,
    ReviewItem,
    Snapshot,
    Source,
)
from app.publish.publisher import publish_scan
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tests.support import html_profiles as _html_profiles  # noqa: F401

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "ingest"
    / "vercel"
    / "html"
    / "vercel-sandbox-pricing"
    / "source.html"
)
URL = "https://vercel.com/docs/sandbox/pricing"

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; provide an isolated PostgreSQL 16 database.",
)

PUBLISHING = PublishingSection(
    automatic_threshold=0.90,
    uncertain_threshold=0.70,
    require_official_source=True,
    require_deterministic_numeric_validation=True,
)


def _alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return config


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    command.upgrade(_alembic_config(), "head")
    created = create_engine(DATABASE_URL)
    try:
        yield created
    finally:
        created.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    created = Session(bind=connection)
    try:
        yield created
    finally:
        created.close()
        transaction.rollback()
        connection.close()


@skip_without_db
def test_matrix_candidate_persists_fact_locations_and_is_held_for_review(
    session: Session,
) -> None:
    provider = Provider(slug="matrix-vercel", name="Matrix Vercel", type="cloud")
    session.add(provider)
    session.flush()
    source = Source(
        provider_id=provider.id,
        slug="vercel-sandbox-pricing",
        adapter_type="html",
        trust_level="official",
        official=True,
        endpoint=URL,
        parser_profile="test_vercel_sandbox_matrix",
        schedule="daily",
    )
    session.add(source)
    session.flush()

    fetcher = FixtureFetcher(
        {URL: (FIXTURE.read_bytes(), "text/html")},
        FetchPolicy(official_domains=("vercel.com",)),
    )
    offers_before = session.scalar(select(func.count()).select_from(Offer))
    versions_before = session.scalar(select(func.count()).select_from(OfferVersion))
    quotas_before = session.scalar(select(func.count()).select_from(Quota))

    scan = run_scan(source, fetcher, session)

    assert scan.status == "success"
    assert (scan.documents_count, scan.candidates_count, scan.errors_count) == (1, 1, 0)
    snapshot = session.scalar(select(Snapshot).where(Snapshot.source_id == source.id))
    candidate = session.scalar(select(Candidate).where(Candidate.scan_run_id == scan.id))
    assert snapshot is not None
    assert candidate is not None
    assert candidate.candidate_facts == {
        "sandbox_active_cpu": "5 hours/month",
        "provisioned_memory": "420 GB-hours/month",
        "sandbox_creations": "5,000/month",
        "service": "Vercel Sandbox",
        "offer_type": "always_free",
        "eligibility": "Hobby plan",
        "exhaustion_behaviour": "hard_stop",
        "notes": (
            "Vercel sends you notifications as you approach your usage quotas. "
            "You will not be charged for any additional usage. Once you exceed "
            "the quotas, sandbox creation is paused until 30 days have passed "
            "since you first used the feature."
        ),
    }
    assert "requires_card" not in candidate.candidate_facts
    assert "has_paid_dependencies" not in candidate.candidate_facts

    evidence = list(session.scalars(select(Evidence).where(Evidence.candidate_id == candidate.id)))
    assert len(evidence) == 8
    assert all(row.official and row.snapshot_id == snapshot.id for row in evidence)
    assert all(row.content_hash == snapshot.content_hash for row in evidence)
    selectors = [row.selector for row in evidence]
    assert any(
        selector
        == (
            "test_vercel_sandbox_matrix matrix row[1:Sandbox Active CPU] "
            "column[Hobby (Included)] -> fact[sandbox_active_cpu]"
        )
        for selector in selectors
    )
    assert any(
        selector == "test_vercel_sandbox_matrix assertion[0] title[0] -> fact[service]"
        for selector in selectors
    )
    assert any(row.excerpt == "Sandbox Creations | 5,000/month" for row in evidence)
    assert any(
        row.excerpt
        == (
            "Vercel sends you notifications as you approach your usage quotas. "
            "You will not be charged for any additional usage. Once you exceed "
            "the quotas, sandbox creation is paused until 30 days have passed "
            "since you first used the feature."
        )
        for row in evidence
    )

    result = publish_scan(session, scan, source, PUBLISHING)

    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert outcome.decision == "review"
    assert outcome.review_item_created is True
    assert "schema_complete" in outcome.failed_conditions
    assert session.scalar(select(func.count()).select_from(Offer)) == offers_before
    assert session.scalar(select(func.count()).select_from(OfferVersion)) == versions_before
    assert session.scalar(select(func.count()).select_from(Quota)) == quotas_before
    review = session.scalar(select(ReviewItem).where(ReviewItem.scan_run_id == scan.id))
    assert review is not None
    assert review.admin_disposition == "pending"
    assert "requires_card" not in review.candidate_facts
