"""Integration tests: a coverage contradiction reaches the F007 review queue.

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL. F008 slice S2
does not build a new admin surface; a declared-vs-derived coverage contradiction
is recorded as an ordinary pending ``review_item`` so it shows up in the existing
``GET /api/admin/review-queue`` and is dispositionable through the existing
action endpoint.

These prove the whole path with real rows:

(a) a provider that declares ``unknown`` over its own published Z0 offer raises
    exactly one pending review item, and the item carries the provider, the
    category, both states and a human-readable explanation;
(b) reconciliation is idempotent -- re-running it does not pile up duplicates;
(c) the item is visible through ``PostgresAdminDataStore.list_review_queue`` and
    can be dispositioned through ``set_review_disposition`` (the same calls the
    admin router makes);
(d) an honest declaration raises nothing, so the queue is not spammed;
(e) the reusable Wave-3 helper ``assert_no_coverage_contradictions`` fails for
    the dishonest provider and passes for the honest one; and
(f) reconciliation NEVER edits the coverage declaration -- the durable artefact
    is the review item, not a rewritten row (decision Q11).

Unlike a mocked admin store these drive the real
:class:`app.admin.data.PostgresAdminDataStore` SQL. It normally opens its own
connection; because ``offer_version`` is append-only at the database level
(a trigger refuses ``DELETE``) these tests must not commit, so the store is
bound to the test's own connection through a tiny Engine-shaped shim and every
row disappears on rollback.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.admin.data import PostgresAdminDataStore
from app.ingest.reconcile_coverage import (
    COVERAGE_MISMATCH_KIND,
    COVERAGE_MISMATCH_REASON,
    assert_no_coverage_contradictions,
    find_coverage_mismatches,
    reconcile_coverage,
)
from app.models.domain import (
    Category,
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    ProviderCategoryCoverage,
    Service,
    Snapshot,
    Source,
)
from app.read_api import queries
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)

HONEST_SLUG = "synthetic-honest-coverage"
DISHONEST_SLUG = "synthetic-dishonest-coverage"
STALE_SLUG = "synthetic-stale-free-claim"
UNCORROBORATED_SLUG = "synthetic-uncorroborated-free-claim"
CATEGORY_SLUG = "object-file-storage"


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


def _seed_provider_with_published_z0(session: Session, slug: str) -> tuple[int, int]:
    """A synthetic provider with one published, truly-free storage offer."""

    category_id = session.execute(
        select(Category.id).where(Category.slug == CATEGORY_SLUG)
    ).scalar_one()
    provider = Provider(slug=slug, name=f"{slug} (synthetic)", type="commercial")
    session.add(provider)
    session.flush()
    svc = Service(
        provider_id=provider.id,
        category_id=category_id,
        canonical_name=f"{slug} object store",
        deployment_model="managed",
    )
    session.add(svc)
    session.flush()
    offer = Offer(
        service_id=svc.id,
        offer_type="always_free",
        zero_cost_class="Z0_TRUE_FREE",
        status="active",
        requires_card=False,
        has_paid_dependencies=False,
        commercial_use_allowed=True,
        personal_use_allowed=True,
    )
    session.add(offer)
    session.flush()
    session.add(
        OfferVersion(
            offer_id=offer.id,
            version_number=1,
            content_hash=f"synthetic-coverage-{slug}",
            offer_type="always_free",
            zero_cost_class="Z0_TRUE_FREE",
            material_facts={
                "confidence": 0.95,
                "confidence_signals": {"completeness": 0.9, "freshness": 0.9},
                "classification": {
                    "zero_cost_class": "Z0_TRUE_FREE",
                    "reasons": ["synthetic fixture reason"],
                    "blocking_conditions": [],
                },
                "gate": {"automatic_threshold": 0.90, "uncertain_threshold": 0.70},
            },
        )
    )
    session.flush()
    return int(provider.id), int(category_id)


def _seed_free_offer_with_evidence(
    session: Session, slug: str, *, fetched_at: datetime, schedule: str = "daily"
) -> tuple[int, int]:
    """A provider with one published Z0 offer whose evidence has a chosen age.

    ``fetched_at`` is what makes this useful: the coverage derivation reads
    staleness from ``evidence -> snapshot.fetched_at`` against the *source's*
    schedule window, so backdating the snapshot is the only honest way to reach
    the ``stale`` derivation without touching the clock the rest of the suite
    runs on.
    """

    provider_id, category_id = _seed_provider_with_published_z0(session, slug)
    version = session.execute(
        select(OfferVersion)
        .join(Offer, Offer.id == OfferVersion.offer_id)
        .join(Service, Service.id == Offer.service_id)
        .where(Service.provider_id == provider_id)
    ).scalar_one()

    source = Source(
        provider_id=provider_id,
        slug=f"{slug}-source",
        adapter_type="html",
        trust_level="official",
        official=True,
        endpoint="https://example.invalid/free",
        schedule=schedule,
    )
    session.add(source)
    session.flush()
    snapshot = Snapshot(
        source_id=source.id,
        content_location=f"memory://{slug}",
        mime_type="text/html",
        content_hash=f"snapshot-{slug}",
        fetched_at=fetched_at,
    )
    session.add(snapshot)
    session.flush()
    session.add(
        Evidence(
            source_id=source.id,
            offer_version_id=version.id,
            snapshot_id=snapshot.id,
            official=True,
            url="https://example.invalid/free",
            content_hash=f"evidence-{slug}",
        )
    )
    session.flush()
    return provider_id, category_id


def _seed_provider_without_offers(session: Session, slug: str) -> tuple[int, int]:
    """A provider that publishes nothing at all in ``CATEGORY_SLUG``."""

    category_id = session.execute(
        select(Category.id).where(Category.slug == CATEGORY_SLUG)
    ).scalar_one()
    provider = Provider(slug=slug, name=f"{slug} (synthetic)", type="commercial")
    session.add(provider)
    session.flush()
    return int(provider.id), int(category_id)


def _matrix_entry(session: Session, *, provider_slug: str, now: datetime):
    """The public matrix cell for (``provider_slug``, ``CATEGORY_SLUG``).

    Read through the same serialiser the read API uses, so a test cannot pass
    against a projection the public catalogue does not actually show.
    """

    from app.read_api import service as read_service

    providers = queries.fetch_providers(session)
    cat_map = queries.category_map_for_providers(session, providers)
    context = queries.coverage_signal_context(session, providers, now=now)
    matrix = read_service.serialize_category_matrix(providers, cat_map, context)
    row = next(r for r in matrix.categories if r.slug == CATEGORY_SLUG)
    return next(e for e in row.providers if e.provider_slug == provider_slug)


class _ConnectionScopedEngine:
    """Presents the ``Engine.begin()`` contract over one existing connection.

    :class:`PostgresAdminDataStore` deliberately uses autonomous transactions so
    the admin UI never piggybacks on a request session. Here we want the exact
    opposite: the store must see the rows this test flushed and leave nothing
    behind, because ``offer_version`` cannot be deleted (an append-only trigger
    refuses it), so the whole test must roll back.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    @contextmanager
    def begin(self) -> Iterator[Connection]:
        # Join the ambient transaction rather than opening a new one, so reads
        # see uncommitted rows and writes vanish on rollback.
        yield self._conn


@pytest.fixture()
def db(engine: Engine) -> Iterator[tuple[Session, PostgresAdminDataStore]]:
    """A rolled-back session plus an admin store reading the same connection."""

    conn = engine.connect()
    trans = conn.begin()
    sess = Session(bind=conn)
    try:
        yield sess, PostgresAdminDataStore(_ConnectionScopedEngine(conn))
    finally:
        sess.close()
        trans.rollback()
        conn.close()


def _pending(store: PostgresAdminDataStore, provider_slug: str) -> list:
    return [
        row
        for row in store.list_review_queue("pending")
        if (row.evidence_conflict or {}).get("kind") == COVERAGE_MISMATCH_KIND
        and (row.evidence_conflict or {}).get("provider_slug") == provider_slug
    ]


# --- (a) a dishonest declaration raises a pending review item ---------------


@skip_without_db
def test_unknown_over_a_published_free_offer_raises_a_review_item(db) -> None:
    session, store = db
    provider_id, category_id = _seed_provider_with_published_z0(session, DISHONEST_SLUG)
    session.add(
        ProviderCategoryCoverage(provider_id=provider_id, category_id=category_id, state="unknown")
    )
    session.flush()

    result = reconcile_coverage(session, now=datetime.now(UTC))
    session.flush()

    ours = [m for m in result.mismatches if m.provider_slug == DISHONEST_SLUG]
    assert len(ours) == 1
    mismatch = ours[0]
    assert mismatch.category_slug == CATEGORY_SLUG
    assert mismatch.declared_state == "unknown"
    assert mismatch.derived_state == "verified_free"

    items = _pending(store, DISHONEST_SLUG)
    assert len(items) == 1, "the contradiction must surface in the existing review queue"
    item = items[0]
    assert item.admin_disposition == "pending"
    assert item.recommended_action == "manual_review"
    assert item.reason.startswith(COVERAGE_MISMATCH_REASON)
    conflict = item.evidence_conflict
    assert conflict["kind"] == COVERAGE_MISMATCH_KIND
    assert conflict["category_slug"] == CATEGORY_SLUG
    assert conflict["declared_state"] == "unknown"
    assert conflict["derived_state"] == "verified_free"
    assert conflict["identity_key"] == f"{DISHONEST_SLUG}/{CATEGORY_SLUG}"
    # The queue must be readable by a human without re-deriving anything.
    assert "unknown" in conflict["explanation"]
    assert "verified_free" in conflict["explanation"]


# --- (b) idempotency --------------------------------------------------------


@skip_without_db
def test_reconciliation_does_not_pile_up_duplicates(db) -> None:
    session, store = db
    provider_id, category_id = _seed_provider_with_published_z0(session, DISHONEST_SLUG)
    session.add(
        ProviderCategoryCoverage(provider_id=provider_id, category_id=category_id, state="unknown")
    )
    session.flush()

    first = reconcile_coverage(session)
    session.flush()
    assert first.created == 1
    assert first.existing == 0

    second = reconcile_coverage(session)
    session.flush()
    assert second.created == 0
    assert second.existing == 1

    assert len(_pending(store, DISHONEST_SLUG)) == 1


# --- (c) dispositionable through the existing admin action -----------------


@skip_without_db
def test_the_item_is_dispositionable_through_the_existing_endpoint(db) -> None:
    session, store = db
    provider_id, category_id = _seed_provider_with_published_z0(session, DISHONEST_SLUG)
    session.add(
        ProviderCategoryCoverage(provider_id=provider_id, category_id=category_id, state="unknown")
    )
    session.flush()
    reconcile_coverage(session)
    session.flush()

    item = _pending(store, DISHONEST_SLUG)[0]

    assert store.set_review_disposition(item.id, "approved", datetime.now(UTC)) is True

    assert _pending(store, DISHONEST_SLUG) == []
    approved = [
        r
        for r in store.list_review_queue("approved")
        if (r.evidence_conflict or {}).get("provider_slug") == DISHONEST_SLUG
    ]
    assert len(approved) == 1
    assert approved[0].id == item.id


# --- (d) an honest declaration raises nothing ------------------------------


@skip_without_db
def test_an_honest_declaration_raises_nothing(db) -> None:
    session, store = db
    provider_id, category_id = _seed_provider_with_published_z0(session, HONEST_SLUG)
    session.add(
        ProviderCategoryCoverage(
            provider_id=provider_id,
            category_id=category_id,
            state="verified_free",
            evidence_url="https://example.invalid/free",
        )
    )
    session.flush()

    result = reconcile_coverage(session)
    session.flush()

    assert [m for m in result.mismatches if m.provider_slug == HONEST_SLUG] == []
    assert _pending(store, HONEST_SLUG) == []


# --- (e) the reusable Wave-3 assertion helper ------------------------------


@skip_without_db
def test_the_wave3_helper_fails_the_dishonest_provider(db) -> None:
    session, _ = db
    provider_id, category_id = _seed_provider_with_published_z0(session, DISHONEST_SLUG)
    session.add(
        ProviderCategoryCoverage(provider_id=provider_id, category_id=category_id, state="unknown")
    )
    session.flush()

    with pytest.raises(AssertionError) as exc:
        assert_no_coverage_contradictions(session, provider_slug=DISHONEST_SLUG)

    message = str(exc.value)
    assert DISHONEST_SLUG in message
    assert CATEGORY_SLUG in message
    assert "unknown" in message
    assert "verified_free" in message


@skip_without_db
def test_the_wave3_helper_passes_the_honest_provider(db) -> None:
    session, _ = db
    provider_id, category_id = _seed_provider_with_published_z0(session, HONEST_SLUG)
    session.add(
        ProviderCategoryCoverage(
            provider_id=provider_id,
            category_id=category_id,
            state="verified_free",
            evidence_url="https://example.invalid/free",
        )
    )
    session.flush()

    assert_no_coverage_contradictions(session, provider_slug=HONEST_SLUG)


@skip_without_db
def test_the_wave3_helper_rejects_an_absent_provider(db) -> None:
    session, _ = db
    with pytest.raises(AssertionError, match="not in the database"):
        assert_no_coverage_contradictions(session, provider_slug="no-such-provider-at-all")


# --- (f) Q11: reconciliation never rewrites the declaration ----------------


@skip_without_db
def test_reconciliation_never_rewrites_the_declaration(db) -> None:
    session, _ = db
    provider_id, category_id = _seed_provider_with_published_z0(session, DISHONEST_SLUG)
    row = ProviderCategoryCoverage(
        provider_id=provider_id, category_id=category_id, state="unknown"
    )
    session.add(row)
    session.flush()
    before = (row.state, row.rationale, row.source_id, row.evidence_url, row.declared_at)

    assert find_coverage_mismatches(session)
    reconcile_coverage(session)
    session.flush()
    session.refresh(row)

    after = (row.state, row.rationale, row.source_id, row.evidence_url, row.declared_at)
    assert after == before, "a contradiction is a question for a human, not an auto-correction"
    assert row.state == "unknown"


# --- (g) f008-obsB: a stale snapshot must not sustain a free claim ----------


@skip_without_db
def test_a_stale_snapshot_unseats_a_published_free_claim(db) -> None:
    """UNSAFE DIRECTION, on real rows: expiry now raises AND changes the display.

    Before this ruling the same rows produced `verified_free` in the public
    matrix and nothing in the queue, so a provider could withdraw its free tier,
    let the snapshot expire, and the catalogue would keep advertising it.
    """

    session, store = db
    now = datetime.now(UTC)
    provider_id, category_id = _seed_free_offer_with_evidence(
        session, STALE_SLUG, fetched_at=now - timedelta(days=400)
    )
    session.add(
        ProviderCategoryCoverage(
            provider_id=provider_id,
            category_id=category_id,
            state="verified_free",
            evidence_url="https://example.invalid/free",
        )
    )
    session.flush()

    entry = _matrix_entry(session, provider_slug=STALE_SLUG, now=now)
    assert entry.derived_state == "stale"
    assert entry.mismatch is True
    assert entry.state == "stale"
    assert entry.state != "verified_free", "an expired snapshot may not sustain a free claim"

    result = reconcile_coverage(session, now=now)
    session.flush()
    ours = [m for m in result.mismatches if m.provider_slug == STALE_SLUG]
    assert len(ours) == 1
    assert ours[0].declared_state == "verified_free"
    assert ours[0].derived_state == "stale"

    items = _pending(store, STALE_SLUG)
    assert len(items) == 1
    assert items[0].evidence_conflict["derived_state"] == "stale"


@skip_without_db
def test_a_fresh_snapshot_leaves_the_same_free_claim_alone(db) -> None:
    """SAFE DIRECTION: the identical rows, one field younger, must not fire.

    This is the control for the test above. It differs from it in exactly one
    value -- the snapshot's ``fetched_at`` -- so a guard that fired on the shape
    of the fixture rather than on staleness would be caught here.
    """

    session, store = db
    now = datetime.now(UTC)
    provider_id, category_id = _seed_free_offer_with_evidence(
        session, STALE_SLUG, fetched_at=now - timedelta(hours=1)
    )
    session.add(
        ProviderCategoryCoverage(
            provider_id=provider_id,
            category_id=category_id,
            state="verified_free",
            evidence_url="https://example.invalid/free",
        )
    )
    session.flush()

    entry = _matrix_entry(session, provider_slug=STALE_SLUG, now=now)
    assert entry.derived_state == "verified_free"
    assert entry.mismatch is False
    assert entry.state == "verified_free"

    result = reconcile_coverage(session, now=now)
    session.flush()
    assert [m for m in result.mismatches if m.provider_slug == STALE_SLUG] == []
    assert _pending(store, STALE_SLUG) == []


# --- (h) f008-obsC: a free claim the catalogue never corroborated -----------


@skip_without_db
def test_a_free_claim_with_no_published_offer_raises_a_review_item(db) -> None:
    """UNSAFE DIRECTION: the claim reaches a human instead of sitting forever."""

    session, store = db
    now = datetime.now(UTC)
    provider_id, category_id = _seed_provider_without_offers(session, UNCORROBORATED_SLUG)
    session.add(
        ProviderCategoryCoverage(
            provider_id=provider_id,
            category_id=category_id,
            state="verified_free",
            evidence_url="https://example.invalid/free",
        )
    )
    session.flush()

    entry = _matrix_entry(session, provider_slug=UNCORROBORATED_SLUG, now=now)
    assert entry.published_offer_count == 0
    assert entry.derived_state == "unknown"
    assert entry.mismatch is True

    result = reconcile_coverage(session, now=now)
    session.flush()
    ours = [m for m in result.mismatches if m.provider_slug == UNCORROBORATED_SLUG]
    assert len(ours) == 1
    assert ours[0].declared_state == "verified_free"
    assert ours[0].derived_state == "unknown"
    assert len(_pending(store, UNCORROBORATED_SLUG)) == 1


@skip_without_db
def test_that_free_claim_is_reviewed_without_being_suppressed(db) -> None:
    """SAFE DIRECTION, and the reason obsC is not obsB.

    An absent publication refutes nothing -- the publication gate withholds
    offers by design -- so the provenance-backed declaration must keep
    displaying while a human reconciles it. Raising the item and changing the
    public claim are two decisions, and only the first is warranted here.
    """

    session, _ = db
    now = datetime.now(UTC)
    provider_id, category_id = _seed_provider_without_offers(session, UNCORROBORATED_SLUG)
    session.add(
        ProviderCategoryCoverage(
            provider_id=provider_id,
            category_id=category_id,
            state="verified_free",
            evidence_url="https://example.invalid/free",
        )
    )
    session.flush()

    entry = _matrix_entry(session, provider_slug=UNCORROBORATED_SLUG, now=now)
    assert entry.state == "verified_free"
    assert entry.state != "conflicting"


@pytest.mark.parametrize("declared", ["offered_no_z0", "not_offered", "unknown"])
@skip_without_db
def test_absence_of_ingest_flags_nothing_but_a_free_claim(db, declared: str) -> None:
    """SAFE DIRECTION: this is where the rejected broad rule's items would live.

    Measured on the merged tree, 93 of 98 provider x category pairs derive
    ``unknown``. Making all of them material is the flood slice S2 refused, and
    re-measuring it here refused it again -- only the free claim is exempted.
    """

    session, store = db
    now = datetime.now(UTC)
    provider_id, category_id = _seed_provider_without_offers(session, HONEST_SLUG)
    session.add(
        ProviderCategoryCoverage(
            provider_id=provider_id,
            category_id=category_id,
            state=declared,
            # `not_offered` is a positive claim, and the schema enforces that it
            # states why; supplying one for every case keeps the three
            # parametrisations identical apart from the state under test.
            rationale=f"synthetic {declared} declaration for the absence-of-ingest control",
            evidence_url="https://example.invalid/probe",
        )
    )
    session.flush()

    entry = _matrix_entry(session, provider_slug=HONEST_SLUG, now=now)
    assert entry.derived_state == "unknown"
    assert entry.mismatch is False
    assert entry.state == declared

    result = reconcile_coverage(session, now=now)
    session.flush()
    assert [m for m in result.mismatches if m.provider_slug == HONEST_SLUG] == []
    assert _pending(store, HONEST_SLUG) == []


# --- (i) the user-facing path: what is actually served over HTTP -----------
#
# Everything above judges the rule. These judge the CATALOGUE, through the same
# `GET /catalogue/categories` route a browser hits, on the REAL clock (the route
# takes no `now` override -- staleness here is produced by backdating the
# snapshot, not by freezing time). The display is where the product harm lands,
# so a review item alone is not evidence that the harm was prevented: these show
# what is on screen WHILE that item sits pending.


def _served_cell(session: Session, *, provider_slug: str) -> dict:
    from app.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).get("/catalogue/categories")
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert response.status_code == 200
    row = next(r for r in response.json()["categories"] if r["slug"] == CATEGORY_SLUG)
    return next(c for c in row["providers"] if c["provider_slug"] == provider_slug)


@skip_without_db
def test_the_public_api_refuses_to_serve_an_expired_free_claim(db) -> None:
    """THE RULING, at the surface a user actually reads.

    A provider that withdraws its free tier cannot reach a user as "verified
    free" through this path: the snapshot expires, and the served cell says
    `stale`. Asserted while the review item is PENDING, because that is exactly
    the window in which a queue-only remedy would still be publishing the wrong
    claim.
    """

    session, store = db
    provider_id, category_id = _seed_free_offer_with_evidence(
        session, STALE_SLUG, fetched_at=datetime.now(UTC) - timedelta(days=400)
    )
    session.add(
        ProviderCategoryCoverage(
            provider_id=provider_id,
            category_id=category_id,
            state="verified_free",
            evidence_url="https://example.invalid/free",
        )
    )
    session.flush()
    reconcile_coverage(session)
    session.flush()
    assert len(_pending(store, STALE_SLUG)) == 1, "the item must be pending for this to be the test"

    cell = _served_cell(session, provider_slug=STALE_SLUG)
    assert cell["declared_state"] == "verified_free"
    assert cell["derived_state"] == "stale"
    assert cell["mismatch"] is True
    assert cell["state"] == "stale"
    assert cell["state"] != "verified_free", (
        "a withdrawn free tier reached a user as a free claim while its review item waited"
    )


@skip_without_db
def test_the_public_api_still_serves_a_fresh_free_claim(db) -> None:
    """THE OTHER DIRECTION. Wrongly withdrawing a real free offer misleads too.

    Identical rows to the test above except for the snapshot's age, so this
    catches a downgrade that fires on the shape of the fixture rather than on
    expiry.
    """

    session, store = db
    provider_id, category_id = _seed_free_offer_with_evidence(
        session, STALE_SLUG, fetched_at=datetime.now(UTC) - timedelta(hours=2)
    )
    session.add(
        ProviderCategoryCoverage(
            provider_id=provider_id,
            category_id=category_id,
            state="verified_free",
            evidence_url="https://example.invalid/free",
        )
    )
    session.flush()
    reconcile_coverage(session)
    session.flush()
    assert _pending(store, STALE_SLUG) == []

    cell = _served_cell(session, provider_slug=STALE_SLUG)
    assert cell["derived_state"] == "verified_free"
    assert cell["mismatch"] is False
    assert cell["state"] == "verified_free"
    assert cell["free_offer_count"] == 1


@skip_without_db
def test_the_public_api_serves_an_uncorroborated_free_claim_while_it_is_queued(db) -> None:
    """THE OTHER DIRECTION for obsC, stated as a disclosed decision.

    This claim IS displayed while its review item waits, deliberately. Its
    evidence has not expired -- the catalogue simply has not published an offer
    in that category, which the publication gate withholds by design. Suppressing
    it would withdraw a provenance-backed free offer on no evidence at all.
    """

    session, store = db
    provider_id, category_id = _seed_provider_without_offers(session, UNCORROBORATED_SLUG)
    session.add(
        ProviderCategoryCoverage(
            provider_id=provider_id,
            category_id=category_id,
            state="verified_free",
            evidence_url="https://example.invalid/free",
        )
    )
    session.flush()
    reconcile_coverage(session)
    session.flush()
    assert len(_pending(store, UNCORROBORATED_SLUG)) == 1

    cell = _served_cell(session, provider_slug=UNCORROBORATED_SLUG)
    assert cell["mismatch"] is True
    assert cell["derived_state"] == "unknown"
    assert cell["published_offer_count"] == 0
    assert cell["state"] == "verified_free"
