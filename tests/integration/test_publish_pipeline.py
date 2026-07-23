"""Integration tests for the deterministic gated publication path (F005 slice 2).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL (the stack smoke
scripts and CI drive this against the live compose Postgres). These prove the
FIRST sanctioned publication path end-to-end against the *real* schema
(migrations 0001..0007) and the immutability / separation triggers:

(a) a high-confidence official Cloudflare offer publishes -> ``offer`` +
    immutable ``offer_version`` + ``quota`` rows, the candidate's official
    ``evidence`` is linked to the new version, and the offer is Z0-classified
    with reasons;
(b) re-publishing identical facts creates NO new ``offer_version`` (idempotent);
(c) a material change appends a NEW ``offer_version`` + a *published*
    ``change_event``;
(d) contradictory official evidence -> a pending ``review_item`` and ZERO new
    published versions;
(e) raw SQL UPDATE/DELETE of an ``offer_version`` is rejected (SQLSTATE 23001);
(f) community (non-official) data can never be published.

Every test runs inside a transaction that is rolled back, leaving data clean.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.classify.engine import Z0_TRUE_FREE
from app.config.loader import load_and_validate
from app.config.models import ProviderConfig, PublishingSection
from app.ingest.reconcile import reconcile_scan
from app.ingest.runner import build_fixture_fetcher, run_provider_scans
from app.ingest.scan import _content_hash, _json_safe
from app.models.domain import (
    Candidate,
    ChangeEvent,
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    Quota,
    ReviewItem,
    ScanRun,
    Snapshot,
    Source,
)
from app.publish.publisher import publish_candidate
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "cloudflare.example.yaml"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ingest" / "cloudflare" / "html"

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)

PUBLISHING = PublishingSection(
    automatic_threshold=0.90,
    uncertain_threshold=0.70,
    require_official_source=True,
    require_deterministic_numeric_validation=True,
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


def _config() -> ProviderConfig:
    model = load_and_validate(str(CONFIG_PATH))
    assert isinstance(model, ProviderConfig)
    return model


# --- synthetic seeding helpers (for change/contradiction/community proofs) ---

_HIGH_CONFIDENCE_FACTS = {
    "service": "Synthetic Service",
    "offer_type": "always_free",
    "requires_card": False,
    "has_paid_dependencies": False,
    "requests_per_day": "100,000/day",
    "cpu_time": "10 ms",
    "exhaustion_behaviour": "request_rejected",
}


def _seed_provider(session: Session) -> Provider:
    provider = Provider(slug="synthetic-cf", name="Synthetic CF", type="cloud")
    session.add(provider)
    session.flush()
    return provider


def _seed_source(session: Session, provider: Provider, slug: str, *, official: bool) -> Source:
    source = Source(
        provider_id=provider.id,
        slug=slug,
        adapter_type="html",
        trust_level="official" if official else "community",
        official=official,
        endpoint="https://developers.cloudflare.com/synthetic/",
        schedule="daily",
    )
    session.add(source)
    session.flush()
    # A fresh snapshot so the freshness gate condition holds.
    session.add(
        Snapshot(
            source_id=source.id,
            content_location=source.endpoint,
            mime_type="text/html",
            content_hash="snap-" + slug,
            fetched_at=datetime.now(UTC),
        )
    )
    session.flush()
    return source


def _seed_candidate(
    session: Session,
    source: Source,
    facts: dict,
    *,
    provider_slug: str = "synthetic-cf",
    official: bool = True,
    with_evidence: bool = True,
) -> Candidate:
    scan_run = ScanRun(source_id=source.id, status="success")
    session.add(scan_run)
    session.flush()

    safe = _json_safe(facts)
    identity = {
        "provider": provider_slug,
        "source_url": source.endpoint,
        "service": facts.get("service"),
        "offer_type": facts.get("offer_type"),
    }
    candidate = Candidate(
        scan_run_id=scan_run.id,
        source_id=source.id,
        provider=provider_slug,
        source_url=source.endpoint,
        verification_state="candidate",
        candidate_facts=safe,
        candidate_key=_content_hash(identity),
        content_hash=_content_hash(facts),
        official=official,
    )
    session.add(candidate)
    session.flush()

    if official and with_evidence:
        snapshot = (
            session.execute(
                select(Snapshot).where(Snapshot.source_id == source.id).order_by(Snapshot.id.desc())
            )
            .scalars()
            .first()
        )
        session.add(
            Evidence(
                source_id=source.id,
                candidate_id=candidate.id,
                snapshot_id=snapshot.id,
                official=True,
                url=source.endpoint,
                content_hash="ev-" + str(candidate.id),
            )
        )
        session.flush()
    return candidate


# --- (a) publish ------------------------------------------------------------


@skip_without_db
def test_publish_creates_offer_version_quota_evidence_and_classifies(session: Session) -> None:
    config = _config()
    fetcher = build_fixture_fetcher(config, FIXTURES_DIR)
    result = run_provider_scans(session, config, fetcher, publish=True)

    outcomes = {o.slug: o for o in result.sources}
    for slug in ("cloudflare-workers-limits", "cloudflare-pages-limits"):
        assert outcomes[slug].status == "scanned", outcomes[slug].error
        assert outcomes[slug].published == 1, outcomes[slug].publish_error or outcomes[slug]

    # Two offers published, each with exactly one immutable version.
    offers = list(session.execute(select(Offer)).scalars())
    assert len(offers) == 2
    for offer in offers:
        versions = list(
            session.execute(select(OfferVersion).where(OfferVersion.offer_id == offer.id)).scalars()
        )
        assert len(versions) == 1
        version = versions[0]
        assert version.version_number == 1
        # Z0 classified with reasons persisted into the immutable material_facts.
        assert offer.zero_cost_class == Z0_TRUE_FREE
        assert version.zero_cost_class == Z0_TRUE_FREE
        assert version.material_facts["classification"]["zero_cost_class"] == Z0_TRUE_FREE
        assert version.material_facts["classification"]["reasons"]
        assert version.material_facts["confidence"] >= 0.90
        # Quota rows exist and carry the evidence-backed exhaustion behaviour.
        quotas = list(
            session.execute(select(Quota).where(Quota.offer_version_id == version.id)).scalars()
        )
        assert quotas
        assert all(
            q.exhaustion_behaviour in ("request_rejected", "deployment_blocked") for q in quotas
        )
        # Official evidence is now linked to the published version.
        linked = list(
            session.execute(
                select(Evidence).where(Evidence.offer_version_id == version.id)
            ).scalars()
        )
        assert linked
        assert all(e.official and e.candidate_id is not None for e in linked)
        # A published ChangeEvent records the addition.
        events = list(
            session.execute(
                select(ChangeEvent).where(
                    ChangeEvent.offer_id == offer.id,
                    ChangeEvent.new_version_id == version.id,
                )
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].change_type == "added"
        assert events[0].publication_status == "published"


# --- (b) idempotent re-publish ---------------------------------------------


@skip_without_db
def test_republish_identical_is_idempotent(session: Session) -> None:
    config = _config()
    fetcher = build_fixture_fetcher(config, FIXTURES_DIR)

    run_provider_scans(session, config, fetcher, publish=True)
    versions_after_first = session.execute(
        select(func.count()).select_from(OfferVersion)
    ).scalar_one()

    second = run_provider_scans(session, config, fetcher, publish=True)
    versions_after_second = session.execute(
        select(func.count()).select_from(OfferVersion)
    ).scalar_one()

    assert versions_after_second == versions_after_first
    second_outcomes = {o.slug: o for o in second.sources}
    for slug in ("cloudflare-workers-limits", "cloudflare-pages-limits"):
        assert second_outcomes[slug].published == 0
        assert second_outcomes[slug].publish_unchanged == 1


# --- (c) material change ----------------------------------------------------


@skip_without_db
def test_material_change_appends_new_version_and_published_change_event(session: Session) -> None:
    provider = _seed_provider(session)
    source = _seed_source(session, provider, "synthetic-official", official=True)

    first = _seed_candidate(session, source, dict(_HIGH_CONFIDENCE_FACTS))
    out1 = publish_candidate(session, first, source, PUBLISHING)
    assert out1.decision == "publish"
    assert out1.version_created is True
    offer_id = out1.offer_id

    changed = dict(_HIGH_CONFIDENCE_FACTS)
    changed["requests_per_day"] = "250,000/day"  # a material quota change
    second = _seed_candidate(session, source, changed)
    out2 = publish_candidate(session, second, source, PUBLISHING)

    assert out2.decision == "publish"
    assert out2.version_created is True
    assert out2.offer_id == offer_id

    versions = list(
        session.execute(
            select(OfferVersion)
            .where(OfferVersion.offer_id == offer_id)
            .order_by(OfferVersion.version_number)
        ).scalars()
    )
    assert [v.version_number for v in versions] == [1, 2]

    modified = list(
        session.execute(
            select(ChangeEvent).where(
                ChangeEvent.offer_id == offer_id,
                ChangeEvent.change_type == "modified",
            )
        ).scalars()
    )
    assert len(modified) == 1
    assert modified[0].publication_status == "published"
    assert modified[0].previous_version_id == versions[0].id
    assert modified[0].new_version_id == versions[1].id


# --- (d) contradiction ------------------------------------------------------


@skip_without_db
def test_contradiction_routes_to_review_not_publish(session: Session) -> None:
    provider = _seed_provider(session)
    source_a = _seed_source(session, provider, "synthetic-a", official=True)
    source_b = _seed_source(session, provider, "synthetic-b", official=True)

    facts_a = dict(_HIGH_CONFIDENCE_FACTS)
    facts_b = dict(_HIGH_CONFIDENCE_FACTS)
    facts_b["requires_card"] = True  # contradicts A on a material fact

    _seed_candidate(session, source_a, facts_a)
    cand_b = _seed_candidate(session, source_b, facts_b)

    # Reconcile source B against A -> a pending contradiction review item.
    scan_b = session.get(ScanRun, cand_b.scan_run_id)
    reconcile_scan(scan_b, source_b, session)

    pending_before = session.execute(
        select(func.count())
        .select_from(ReviewItem)
        .where(ReviewItem.admin_disposition == "pending")
    ).scalar_one()
    assert pending_before >= 1

    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    outcome = publish_candidate(session, cand_b, source_b, PUBLISHING, scan_run_id=scan_b.id)
    assert outcome.decision == "review"
    assert outcome.version_created is False

    versions_after = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()
    assert versions_after == versions_before  # ZERO new published versions
    # A pending review item still stands.
    pending_after = session.execute(
        select(func.count())
        .select_from(ReviewItem)
        .where(ReviewItem.admin_disposition == "pending")
    ).scalar_one()
    assert pending_after >= 1


# --- (e) offer_version immutability (raw SQL) -------------------------------


@skip_without_db
def test_offer_version_update_and_delete_rejected_23001(session: Session) -> None:
    provider = _seed_provider(session)
    source = _seed_source(session, provider, "synthetic-immutable", official=True)
    candidate = _seed_candidate(session, source, dict(_HIGH_CONFIDENCE_FACTS))
    outcome = publish_candidate(session, candidate, source, PUBLISHING)
    assert outcome.version_created is True
    version_id = outcome.offer_version_id

    with pytest.raises(IntegrityError) as update_exc:
        with session.begin_nested():
            session.execute(
                text(
                    "UPDATE offer_version SET version_number = version_number + 100 WHERE id = :i"
                ),
                {"i": version_id},
            )
    assert getattr(update_exc.value.orig, "sqlstate", None) == "23001" or "23001" in str(
        update_exc.value
    )

    with pytest.raises(IntegrityError) as delete_exc:
        with session.begin_nested():
            session.execute(text("DELETE FROM offer_version WHERE id = :i"), {"i": version_id})
    assert getattr(delete_exc.value.orig, "sqlstate", None) == "23001" or "23001" in str(
        delete_exc.value
    )


# --- (f) community cannot publish -------------------------------------------


@skip_without_db
def test_community_candidate_cannot_publish(session: Session) -> None:
    provider = _seed_provider(session)
    community_source = _seed_source(session, provider, "synthetic-community", official=False)
    candidate = _seed_candidate(
        session,
        community_source,
        dict(_HIGH_CONFIDENCE_FACTS),
        official=False,
        with_evidence=False,
    )

    offers_before = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    outcome = publish_candidate(session, candidate, community_source, PUBLISHING)

    assert outcome.decision == "withhold"
    assert outcome.version_created is False
    assert outcome.offer_id is None
    offers_after = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    assert offers_after == offers_before
