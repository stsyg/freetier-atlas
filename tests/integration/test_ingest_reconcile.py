"""Integration tests for reconciliation (F004 Slice 3, migration 0005).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL (the stack smoke
scripts and CI drive this against the live compose Postgres). Exercises the
end-to-end reconciliation persistence path through
:func:`app.ingest.reconcile.reconcile_scan` against a real database, over the
four F004 acceptance scenarios:

* **unchanged** -- re-scanning byte-identical input and reconciling produces no
  ``change_event`` and no ``review_item``;
* **changed** -- a modified candidate yields exactly one DRAFT ``change_event``
  with the correct ``change_type`` + ``materiality``;
* **stale** -- when the source's freshest snapshot is older than its schedule
  window, the candidates are flagged ``verification_state='stale'``;
* **contradictory** -- two official sources disagreeing on a material fact raise a
  ``review_item`` with ``admin_disposition='pending'`` and nothing is
  auto-resolved.

Across every scenario, **no** ``offer`` / ``offer_version`` row is ever created
(reconciliation has no publication path). Each test runs inside a transaction
that is rolled back, leaving the schema and data clean.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ingest.fetch import FetchPolicy, FixtureFetcher
from app.ingest.reconcile import reconcile_scan
from app.ingest.scan import run_scan
from app.models.domain import (
    Candidate,
    ChangeEvent,
    Offer,
    OfferVersion,
    ReviewItem,
    Source,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

ENDPOINT_A = "https://provider-a.example/offers.json"
ENDPOINT_B = "https://provider-b.example/offers.json"
POLICY = FetchPolicy(official_domains=("provider-a.example", "provider-b.example"))

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)


def _document(*, requires_card: bool, extra_service: bool = False) -> dict:
    offers = [
        {
            "service": "Widgets",
            "offer_type": "always_free",
            "requires_card": requires_card,
            "has_paid_dependencies": False,
            "quotas": [{"metric": "requests", "exhaustion_behaviour": "hard_stop"}],
        }
    ]
    if extra_service:
        offers.append(
            {
                "service": "Gadgets",
                "offer_type": "trial",
                "requires_card": True,
                "has_paid_dependencies": False,
                "quotas": [{"metric": "builds", "exhaustion_behaviour": "throttled"}],
            }
        )
    return {"provider": "example", "offers": offers}


def _fetcher(endpoint: str, document: dict) -> FixtureFetcher:
    payload = json.dumps(document).encode("utf-8")
    return FixtureFetcher({endpoint: (payload, "application/json")}, POLICY)


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


def _make_source(session: Session, *, endpoint: str, schedule: str | None = None) -> Source:
    source = Source(
        adapter_type="reference-json",
        trust_level="official",
        official=True,
        endpoint=endpoint,
        enabled=True,
        schedule=schedule,
    )
    session.add(source)
    session.flush()
    return source


def _change_events_for(session: Session, candidate_ids: list[int]) -> list[ChangeEvent]:
    return list(
        session.execute(
            select(ChangeEvent).where(
                (ChangeEvent.new_candidate_id.in_(candidate_ids))
                | (ChangeEvent.previous_candidate_id.in_(candidate_ids))
            )
        ).scalars()
    )


def _candidate_ids(session: Session, scan_run_id: int) -> list[int]:
    return [
        c.id
        for c in session.execute(
            select(Candidate).where(Candidate.scan_run_id == scan_run_id)
        ).scalars()
    ]


def _assert_no_publication(session: Session, *, offers_before: int, versions_before: int) -> None:
    offers_after = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_after = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()
    assert offers_after == offers_before
    assert versions_after == versions_before


@skip_without_db
def test_unchanged_rescan_reconciles_to_no_change_or_review(session: Session) -> None:
    source = _make_source(session, endpoint=ENDPOINT_A)
    offers_before = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    first = run_scan(source, _fetcher(ENDPOINT_A, _document(requires_card=False)), session)
    reconcile_scan(first, source, session)  # first observation -> 'added'

    second = run_scan(source, _fetcher(ENDPOINT_A, _document(requires_card=False)), session)
    result = reconcile_scan(second, source, session)

    # Unchanged candidate on re-scan -> nothing new recorded.
    assert result.change_events == 0
    assert result.review_items == 0
    second_ids = _candidate_ids(session, second.id)
    assert _change_events_for(session, second_ids) == []
    _assert_no_publication(session, offers_before=offers_before, versions_before=versions_before)


@skip_without_db
def test_changed_candidate_yields_single_draft_material_change_event(session: Session) -> None:
    source = _make_source(session, endpoint=ENDPOINT_A)
    offers_before = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    first = run_scan(
        source, _fetcher(ENDPOINT_A, _document(requires_card=False, extra_service=True)), session
    )
    reconcile_scan(first, source, session)

    # Widgets flips requires_card True; Gadgets is unchanged.
    second = run_scan(
        source, _fetcher(ENDPOINT_A, _document(requires_card=True, extra_service=True)), session
    )
    result = reconcile_scan(second, source, session)

    assert result.modified == 1
    assert result.change_events == 1

    second_ids = _candidate_ids(session, second.id)
    events = _change_events_for(session, second_ids)
    assert len(events) == 1
    event = events[0]
    assert event.change_type == "modified"
    assert event.materiality == "material"
    assert event.publication_status == "draft"
    assert event.offer_id is None
    assert event.new_candidate_id in second_ids
    assert event.previous_candidate_id is not None

    _assert_no_publication(session, offers_before=offers_before, versions_before=versions_before)


@skip_without_db
def test_stale_source_flags_candidates_and_is_not_fresh(session: Session) -> None:
    source = _make_source(session, endpoint=ENDPOINT_A, schedule="daily")
    offers_before = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    scan = run_scan(source, _fetcher(ENDPOINT_A, _document(requires_card=False)), session)

    # Reconcile "well after" the snapshot was fetched: beyond the daily window.
    future = datetime.now(UTC) + timedelta(days=5)
    result = reconcile_scan(scan, source, session, now=future)

    assert result.stale_candidates >= 1
    candidates = list(
        session.execute(select(Candidate).where(Candidate.scan_run_id == scan.id)).scalars()
    )
    assert candidates
    assert all(c.verification_state == "stale" for c in candidates)

    _assert_no_publication(session, offers_before=offers_before, versions_before=versions_before)


@skip_without_db
def test_contradictory_official_sources_raise_pending_review_item(session: Session) -> None:
    source_a = _make_source(session, endpoint=ENDPOINT_A)
    source_b = _make_source(session, endpoint=ENDPOINT_B)
    offers_before = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    reviews_before = session.execute(select(func.count()).select_from(ReviewItem)).scalar_one()

    # Source A says the Widgets always-free offer needs no card...
    scan_a = run_scan(source_a, _fetcher(ENDPOINT_A, _document(requires_card=False)), session)
    reconcile_scan(scan_a, source_a, session)

    # ...Source B says the same offer DOES require a card -> a material conflict.
    scan_b = run_scan(source_b, _fetcher(ENDPOINT_B, _document(requires_card=True)), session)
    result = reconcile_scan(scan_b, source_b, session)

    assert result.review_items == 1
    reviews = list(
        session.execute(select(ReviewItem).where(ReviewItem.scan_run_id == scan_b.id)).scalars()
    )
    assert len(reviews) == 1
    review = reviews[0]
    # Nothing is auto-resolved: the disposition is pending and no offer exists.
    assert review.admin_disposition == "pending"
    assert review.recommended_action == "manual_review"
    assert review.offer_id is None
    conflict_fields = {c["field"] for c in review.evidence_conflict["conflicts"]}
    assert "requires_card" in conflict_fields

    reviews_after = session.execute(select(func.count()).select_from(ReviewItem)).scalar_one()
    assert reviews_after == reviews_before + 1

    _assert_no_publication(session, offers_before=offers_before, versions_before=versions_before)


@skip_without_db
def test_reconcile_never_creates_offer_version(session: Session) -> None:
    source = _make_source(session, endpoint=ENDPOINT_A)
    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    first = run_scan(source, _fetcher(ENDPOINT_A, _document(requires_card=False)), session)
    reconcile_scan(first, source, session)
    second = run_scan(source, _fetcher(ENDPOINT_A, _document(requires_card=True)), session)
    reconcile_scan(second, source, session)

    versions_after = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()
    assert versions_after == versions_before
