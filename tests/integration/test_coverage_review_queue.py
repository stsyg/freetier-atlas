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
from datetime import UTC, datetime
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
    Offer,
    OfferVersion,
    Provider,
    ProviderCategoryCoverage,
    Service,
)
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
