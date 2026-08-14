"""Offline Azure provider pipeline against a real PostgreSQL database.

Every byte here comes from a committed fixture through :class:`FixtureFetcher`;
no socket is opened. What the database adds is the part that cannot be proved
from one document: that seven sources persist seven independent candidates, that
NONE of them publishes an offer, and that a change to a published quota surfaces
as a draft change event instead of overwriting the previous value.

The publication assertion is the point. Microsoft markets a LIFETIME Cosmos DB
free tier and this slice extracts it -- but usage above that lifetime allowance
is billed at regular price, a payment method is required to open an Azure free
account, and no Azure document in this slice states that a card is unnecessary
for any per-service free plan. The gate must therefore withhold every Azure
offer, including the perpetual one.
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
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "azure.example.yaml"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ingest" / "azure" / "html"
DOMAINS = ("azure.microsoft.com", "learn.microsoft.com")
SERVICES = (
    "Azure free account",
    "Azure 12 months free services",
    "Azure Cosmos DB",
    "Azure App Service",
    "Azure Static Web Apps",
    "Azure DevOps Services",
    "Azure for Students",
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
def test_runner_persists_seven_official_review_candidates_and_zero_offers(
    session: Session, config
) -> None:
    snapshots_before = session.scalar(select(func.count()).select_from(Snapshot))
    result = _run(session, config)
    assert result.scanned == 7
    assert result.failed == 0
    # THE POINT OF THIS SLICE. Azure requires a payment method to open a free
    # account, bills above its LIFETIME Cosmos DB allowance, and states nothing
    # about cards on its per-service free plans, so the publication gate must
    # withhold every one of these -- including the perpetual tier.
    assert result.total_published == 0
    assert result.total_reviewed == 7

    provider = session.scalar(select(Provider).where(Provider.slug == "azure"))
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

    assert len(sources) == 7
    assert len(candidates) == 7
    assert session.scalar(select(func.count()).select_from(Snapshot)) - snapshots_before == 7
    assert {row.candidate_facts["service"] for row in reviews} == set(SERVICES)
    assert {row.candidate_id for row in evidence} == {row.id for row in candidates}
    assert all(
        row.url.startswith(("https://azure.microsoft.com/", "https://learn.microsoft.com/"))
        for row in evidence
    )
    assert all(row.admin_disposition == "pending" for row in reviews)

    # Seven sources, seven distinct documents. No Azure offer in this provider
    # shares a page with another, so none needs a section anchor to stay
    # distinct.
    endpoints = {row.endpoint for row in sources}
    assert len(endpoints) == 7

    azure_offer_ids = select(Offer.id).join(Service).where(Service.provider_id == provider.id)
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
            .where(OfferVersion.offer_id.in_(azure_offer_ids))
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(Quota)
            .join(OfferVersion, Quota.offer_version_id == OfferVersion.id)
            .where(OfferVersion.offer_id.in_(azure_offer_ids))
        )
        == 0
    )


@skip_without_db
def test_the_four_offer_kinds_persist_as_distinct_offers(session: Session, config) -> None:
    result = _run(session, config, publish=False)
    run_ids = [outcome.scan_run_id for outcome in result.sources]
    candidates = list(session.scalars(select(Candidate).where(Candidate.scan_run_id.in_(run_ids))))
    by_service = {row.candidate_facts["service"]: row.candidate_facts for row in candidates}

    assert by_service["Azure free account"]["offer_type"] == "new_customer_credit"
    assert by_service["Azure 12 months free services"]["offer_type"] == "trial"
    assert by_service["Azure for Students"]["offer_type"] == "student_program"
    assert by_service["Azure DevOps Services"]["offer_type"] == "recurring_quota"
    assert by_service["Azure Cosmos DB"]["offer_type"] == "always_free"
    assert by_service["Azure App Service"]["offer_type"] == "other"
    assert by_service["Azure Static Web Apps"]["offer_type"] == "other"

    # The credit belongs to the account-level offer and to nothing else.
    assert by_service["Azure free account"]["credit_amount"] == "$200"
    assert "credit_amount" not in by_service["Azure Cosmos DB"]
    assert "credit_amount" not in by_service["Azure App Service"]
    # The perpetual offer is still billed on overage.
    assert by_service["Azure Cosmos DB"]["exhaustion_behaviour"] == "automatic_billing"
    # The one SAFE exhaustion behaviour survives persistence unchanged.
    assert by_service["Azure App Service"]["exhaustion_behaviour"] == "site_disabled_until_reset"
    # The payment-method requirement is recorded where it is quoted, and nowhere
    # does an Azure candidate claim a card is NOT required.
    assert by_service["Azure free account"]["requires_card"] is True
    assert all(row.candidate_facts.get("requires_card") is not False for row in candidates)
    # The favourable Students bullet IS persisted, as a note rather than a gate.
    assert by_service["Azure for Students"]["card_claim"] == "No credit card required"
    assert "requires_card" not in by_service["Azure for Students"]


@skip_without_db
def test_the_perpetual_offer_is_persisted_but_never_published(session: Session, config) -> None:
    """The single riskiest row in this provider, checked at the pipeline level.

    An offer Microsoft itself calls lifetime is exactly the one a reader would
    most readily believe is free. It must reach the review queue and stop there.
    """

    result = _run(session, config)
    run_ids = [outcome.scan_run_id for outcome in result.sources]
    candidates = list(session.scalars(select(Candidate).where(Candidate.scan_run_id.in_(run_ids))))
    cosmos = next(row for row in candidates if row.candidate_facts["service"] == "Azure Cosmos DB")
    assert cosmos.candidate_facts["offer_type"] == "always_free"
    assert "lasts indefinitely" in cosmos.candidate_facts["availability"]
    assert cosmos.candidate_facts["exhaustion_behaviour"] == "automatic_billing"

    review = session.scalar(
        select(ReviewItem).where(
            ReviewItem.candidate_facts["service"].as_string() == "Azure Cosmos DB",
            ReviewItem.admin_disposition == "pending",
        )
    )
    assert review is not None
    provider = session.scalar(select(Provider).where(Provider.slug == "azure"))
    published = session.scalar(
        select(func.count())
        .select_from(Offer)
        .join(Service)
        .where(Service.provider_id == provider.id, Service.canonical_name == "Azure Cosmos DB")
    )
    assert published == 0


@skip_without_db
def test_changed_case_creates_a_draft_change_event(session: Session, config) -> None:
    _run(session, config, publish=False)
    source = session.scalar(select(Source).where(Source.slug == "azure-app-service-quotas"))
    body = (FIXTURES / "changed" / "source.html").read_bytes()
    fetcher = FixtureFetcher(
        {source.endpoint: (body, "text/html")}, FetchPolicy(official_domains=DOMAINS)
    )
    scan = run_scan(source, fetcher, session)
    reconcile_scan(scan, source, session, now=datetime(2026, 8, 14, 23, 0, tzinfo=UTC))
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
    source = session.scalar(select(Source).where(Source.slug == "azure-app-service-quotas"))
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
    source = session.scalar(select(Source).where(Source.slug == "azure-app-service-quotas"))
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


@skip_without_db
def test_all_fourteen_coverage_rows_persist_with_three_evidence_backed(
    session: Session, config
) -> None:
    """The declaration is a database invariant, not only a config-load one."""

    from app.ingest.config_sync import sync_provider
    from app.models.domain import Category, ProviderCategoryCoverage

    sync_provider(session, config)
    session.flush()
    provider = session.scalar(select(Provider).where(Provider.slug == "azure"))
    rows = list(
        session.scalars(
            select(ProviderCategoryCoverage).where(
                ProviderCategoryCoverage.provider_id == provider.id
            )
        )
    )
    assert len(rows) == 14
    assert sum(row.state == "offered_no_z0" for row in rows) == 3
    assert sum(row.state == "unknown" for row in rows) == 11
    assert all(row.state != "verified_free" for row in rows)
    assert all(row.state != "not_offered" for row in rows)

    evidence_backed = [row for row in rows if row.state == "offered_no_z0"]
    assert all(row.source_id is not None for row in evidence_backed)
    slugs = {
        session.scalar(select(Category.slug).where(Category.id == row.category_id))
        for row in evidence_backed
    }
    assert slugs == {"containers-app-hosting", "nosql-key-value", "cicd-source-control"}


_PIPELINE_OFFERS: tuple[Mapping[str, object], ...] = (
    {
        "service": "Azure Cosmos DB",
        "offer_type": "always_free",
        "requires_card": None,
        "has_paid_dependencies": None,
        "quotas": [
            {
                "metric": "free_request_units_per_second",
                "exhaustion_behaviour": "automatic_billing",
            }
        ],
    },
    {
        "service": "Azure free account",
        "offer_type": "new_customer_credit",
        "requires_card": True,
        "has_paid_dependencies": None,
        "quotas": [{"metric": "azure_credits", "exhaustion_behaviour": "manual_upgrade_required"}],
    },
)


def _pipeline_source(session: Session) -> Source:
    source = Source(
        adapter_type="reference-json",
        trust_level="official",
        official=True,
        endpoint="https://azure.microsoft.com/en-us/pricing/f008-pipeline-case",
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
        now=datetime(2026, 8, 14, 23, 0, tzinfo=UTC),
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
