"""Offline Oracle provider pipeline against a real PostgreSQL database.

Every byte here comes from a committed fixture through :class:`FixtureFetcher`;
no socket is opened. What the database adds is the part that cannot be proved
from one document: that six sources persist six independent candidates, that
NONE of them publishes an offer, and that a change to a published limit surfaces
as a draft change event instead of overwriting the previous value.

The publication assertion is the point, and it must be stated precisely. Oracle
markets "Always Free" more loudly than any other provider in this repository, and
its tier really is perpetual -- this slice extracts three ``always_free`` offers
and says so. What withholds Z0 is a payment card, quoted verbatim on four
independent Oracle documents.

**Publication and Z0 are different gates, and this module does not conflate
them.** MEASURED: all six Oracle candidates are held for review as "uncertain
evidence", and the reason is that their facts are pinned to prose rather than to
numeric quota rows, so they fail the gate's ``schema_complete`` and
``deterministic`` hard conditions. It is NOT the card that stops publication --
a Z1 offer is a perfectly legitimate catalogue entry, it is simply not free.
An earlier draft of this module claimed the card was doing that work; it was
measured and corrected. The card's effect is on the ZERO-COST CLASS, and
``test_the_catalogue_never_labels_a_card_required_offer_z0`` is where that is
proved, with a control showing the Z0 label is reachable through the same path.
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

from tests.support.fixtures import drive_stale, drive_withdrawn, json_fetcher

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "oracle.example.yaml"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ingest" / "oracle" / "html"
DOMAINS = ("oracle.com", "www.oracle.com", "docs.oracle.com")
SERVICES = (
    "Oracle Cloud Infrastructure Always Free",
    "Oracle Cloud Infrastructure Free Trial",
    "Oracle Cloud Always Free services",
    "Oracle Cloud Free Tier",
    "Oracle Cloud Free Credit Promotion",
    "Oracle MySQL HeatWave",
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
    # MEASURED, and stated precisely: nothing is published and all six are held
    # for review as uncertain evidence. The reason is that Oracle publishes its
    # free-tier terms as PROSE, so these candidates carry no numeric quota rows
    # and fail the gate's `schema_complete` and `deterministic` hard conditions.
    # This is NOT the card gate -- see the module docstring.
    assert result.total_published == 0
    assert result.total_reviewed == 6

    provider = session.scalar(select(Provider).where(Provider.slug == "oracle"))
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
    assert all(
        row.url.startswith(("https://www.oracle.com/", "https://docs.oracle.com/"))
        for row in evidence
    )
    assert all(row.admin_disposition == "pending" for row in reviews)

    # Six sources, six distinct documents. No Oracle offer in this provider shares
    # a page with another, so none needs a section anchor to stay distinct.
    endpoints = {row.endpoint for row in sources}
    assert len(endpoints) == 6

    oracle_offer_ids = select(Offer.id).join(Service).where(Service.provider_id == provider.id)
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
            .where(OfferVersion.offer_id.in_(oracle_offer_ids))
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(Quota)
            .join(OfferVersion, Quota.offer_version_id == OfferVersion.id)
            .where(OfferVersion.offer_id.in_(oracle_offer_ids))
        )
        == 0
    )


@skip_without_db
def test_the_perpetual_and_credit_offers_persist_as_distinct_offers(
    session: Session, config
) -> None:
    result = _run(session, config, publish=False)
    run_ids = [outcome.scan_run_id for outcome in result.sources]
    candidates = list(session.scalars(select(Candidate).where(Candidate.scan_run_id.in_(run_ids))))
    by_service = {row.candidate_facts["service"]: row.candidate_facts for row in candidates}

    # The three genuinely perpetual offers, evidenced rather than assumed away.
    assert by_service["Oracle Cloud Infrastructure Always Free"]["offer_type"] == "always_free"
    assert by_service["Oracle Cloud Always Free services"]["offer_type"] == "always_free"
    assert by_service["Oracle MySQL HeatWave"]["offer_type"] == "always_free"
    # The three credit-backed, time-limited offers.
    assert (
        by_service["Oracle Cloud Infrastructure Free Trial"]["offer_type"] == "new_customer_credit"
    )
    assert by_service["Oracle Cloud Free Tier"]["offer_type"] == "new_customer_credit"
    assert by_service["Oracle Cloud Free Credit Promotion"]["offer_type"] == "new_customer_credit"

    # A credit belongs to the credit offers and to nothing else.
    assert by_service["Oracle Cloud Infrastructure Free Trial"]["credit_amount"] == "$300"
    assert "credit_amount" not in by_service["Oracle Cloud Always Free services"]
    assert "credit_amount" not in by_service["Oracle MySQL HeatWave"]

    # The card requirement is recorded where it is QUOTED, on four documents, and
    # nowhere does an Oracle candidate claim a card is NOT required.
    carded = [row for row in candidates if row.candidate_facts.get("requires_card") is True]
    assert len(carded) == 4
    assert all(row.candidate_facts.get("requires_card") is not False for row in candidates)
    # The two that stay silent do so because their documents say nothing about
    # payment -- an ABSENCE, which is weaker than a quotation and is named as such.
    silent = {
        row.candidate_facts["service"]
        for row in candidates
        if row.candidate_facts.get("requires_card") is None
    }
    assert silent == {
        "Oracle Cloud Infrastructure Always Free",
        "Oracle Cloud Free Credit Promotion",
    }


@skip_without_db
def test_changed_case_creates_a_draft_change_event(session: Session, config) -> None:
    _run(session, config, publish=False)
    source = session.scalar(select(Source).where(Source.slug == "oracle-always-free-resources"))
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
    """The evidence floor, enforced at the pipeline level rather than in a unit test.

    The `partial` document still carries the whole limits table and every
    allowance paragraph; only the sentence that makes the tier perpetual is gone.
    A reader that trusted the table alone would still call it Always Free. The
    pipeline persists nothing instead.
    """

    _run(session, config, publish=False)
    source = session.scalar(select(Source).where(Source.slug == "oracle-always-free-resources"))
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
    source = session.scalar(select(Source).where(Source.slug == "oracle-always-free-resources"))
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
        "service": "Oracle Cloud Always Free services",
        "offer_type": "always_free",
        "requires_card": True,
        "has_paid_dependencies": None,
        "quotas": [
            {
                "metric": "always_free_services",
                "exhaustion_behaviour": "request_rejected",
            }
        ],
    },
    {
        "service": "Oracle Cloud Infrastructure Free Trial",
        "offer_type": "new_customer_credit",
        "requires_card": True,
        "has_paid_dependencies": None,
        "quotas": [{"metric": "free_trial_credits", "exhaustion_behaviour": "resource_reclaimed"}],
    },
)


def _pipeline_source(session: Session) -> Source:
    source = Source(
        adapter_type="reference-json",
        trust_level="official",
        official=True,
        endpoint="https://www.oracle.com/cloud/free/f008-pipeline-case",
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


@skip_without_db
def test_the_catalogue_never_labels_a_card_required_offer_z0(session: Session, config) -> None:
    """NON-VACUITY CONTROL for the Z0 claim, at the pipeline level.

    Two candidates are attached to the SAME real scan run, on the same real
    source, and put through the SAME ``publish_scan`` call with the same
    publishing thresholds and the same frozen clock. They differ only in the one
    fact Oracle's documents actually decide: whether a payment card is required.

    BOTH publish -- a Z1 offer is a perfectly legitimate catalogue entry, it is
    simply not free -- and that is what makes this a control rather than a
    tautology. The cleared one is labelled ``Z0_TRUE_FREE`` and the
    Oracle-shaped one ``Z1_BILLING_EXPOSURE``. So the Z0 label is demonstrably
    reachable through this exact path, and Oracle's evidence is what withholds it.

    The facts are attached directly rather than routed through an adapter because
    the reference-JSON adapter carries no numeric quota amounts, so nothing that
    goes through it can ever satisfy the gate's ``deterministic`` condition. That
    is the same reason the repository's own publish-pipeline test seeds its
    candidates, and it is stated here rather than left for a reader to rediscover.
    """

    from app.ingest.scan import _content_hash
    from app.publish.publisher import publish_scan

    provider = session.scalar(select(Provider).where(Provider.slug == "oracle"))
    if provider is None:
        provider = Provider(slug="oracle", name="Oracle Cloud Infrastructure", type="cloud")
        session.add(provider)
        session.flush()

    source = Source(
        provider_id=provider.id,
        slug="oracle-publish-control",
        adapter_type="reference-json",
        trust_level="official",
        official=True,
        endpoint="https://www.oracle.com/cloud/free/f008-publish-control",
        enabled=True,
        schedule="daily",
    )
    session.add(source)
    session.flush()
    url = source.endpoint or ""
    now = datetime(2026, 8, 14, 20, 0, tzinfo=UTC)

    # A real scan, so a real ScanRun and Snapshot exist to hang evidence from.
    scan = run_scan(
        session=session, source=source, fetcher=json_fetcher(url, {"offers": []}, domains=DOMAINS)
    )
    snapshot = session.scalars(
        select(Snapshot).where(Snapshot.source_id == source.id).order_by(Snapshot.id.desc())
    ).first()
    assert snapshot is not None

    base = {
        "offer_type": "always_free",
        "requests_per_day": "100,000/day",
        "cpu_time": "10 ms",
        "exhaustion_behaviour": "request_rejected",
    }
    oracle_shaped = {
        **base,
        "service": "Oracle-shaped control (card required, as Oracle states)",
        "requires_card": True,
        "has_paid_dependencies": False,
    }
    cleared = {
        **base,
        "service": "Cleared control (no card, as no Oracle document states)",
        "requires_card": False,
        "has_paid_dependencies": False,
    }

    seeded: dict[str, int] = {}
    for facts in (oracle_shaped, cleared):
        candidate = Candidate(
            scan_run_id=scan.id,
            source_id=source.id,
            provider="oracle",
            source_url=url,
            verification_state="candidate",
            candidate_facts=facts,
            candidate_key=_content_hash(
                {
                    "provider": "oracle",
                    "source_url": url,
                    "service": facts["service"],
                    "offer_type": facts["offer_type"],
                }
            ),
            content_hash=_content_hash(facts),
            official=True,
        )
        session.add(candidate)
        session.flush()
        seeded[str(facts["service"])] = candidate.id
        session.add(
            Evidence(
                source_id=source.id,
                candidate_id=candidate.id,
                snapshot_id=snapshot.id,
                official=True,
                url=url,
                content_hash=f"evidence-{candidate.id}",
            )
        )
    session.flush()

    outcome = publish_scan(session, scan, source, config.publishing, now=now)
    by_candidate = {o.candidate_id: o for o in outcome.outcomes}

    # BOTH publish. A Z1 offer is a legitimate catalogue entry -- it is simply not
    # free -- so publication is not the Z0 gate and must not be read as one.
    assert outcome.published == 2, [(o.candidate_id, o.failed_conditions) for o in outcome.outcomes]

    cleared_outcome = by_candidate[seeded[str(cleared["service"])]]
    oracle_outcome = by_candidate[seeded[str(oracle_shaped["service"])]]

    # THE CONTROL: the Z0 label is reachable through this exact path...
    assert cleared_outcome.decision == "publish"
    assert cleared_outcome.zero_cost_class == "Z0_TRUE_FREE"
    assert cleared_outcome.offer_id is not None

    # ...and a quoted card requirement is what withholds it. The offer is still
    # catalogued, correctly labelled, rather than silently dropped.
    assert oracle_outcome.decision == "publish"
    assert oracle_outcome.zero_cost_class == "Z1_BILLING_EXPOSURE"
    assert oracle_outcome.offer_id is not None
    assert oracle_outcome.offer_id != cleared_outcome.offer_id
