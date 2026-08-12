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

F008 S3 adds the two Q7-A carry-overs:

* **idempotency** -- re-invoking ``reconcile_scan`` for the same ``scan_run``
  (a retried job, a resumed CLI run) records each transition exactly once;
* **unknown materiality, end to end** -- a real HTML provider profile changes a
  field that is in neither the material nor the cosmetic set, and the resulting
  ``change_event`` is classified ``unknown`` and never auto-promoted by the gate.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.config.models import PublishingSection
from app.ingest.fetch import FetchPolicy, FixtureFetcher
from app.ingest.reconcile import classify_materiality, reconcile_scan
from app.ingest.scan import run_scan
from app.models.domain import (
    Candidate,
    ChangeEvent,
    Offer,
    OfferVersion,
    Provider,
    ReviewItem,
    Source,
)
from app.publish.publisher import publish_scan
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, aliased

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

ENDPOINT_A = "https://provider-a.example/offers.json"
ENDPOINT_B = "https://provider-b.example/offers.json"
POLICY = FetchPolicy(official_domains=("provider-a.example", "provider-b.example"))

PUBLISHING = PublishingSection(
    automatic_threshold=0.90,
    uncertain_threshold=0.70,
    require_official_source=True,
    require_deterministic_numeric_validation=True,
)

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)


def _document(
    *, requires_card: bool, extra_service: bool = False, duplicate_extra: bool = False
) -> dict:
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
        if duplicate_extra:
            # The same (service, offer_type) identity listed a second time with
            # different quota detail -- an ordinary official-document shape, and
            # one that yields two candidate rows sharing a candidate_key.
            offers.append(
                {
                    "service": "Gadgets",
                    "offer_type": "trial",
                    "requires_card": True,
                    "has_paid_dependencies": False,
                    "quotas": [{"metric": "bandwidth", "exhaustion_behaviour": "hard_stop"}],
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


# --- Q7-A carry-over (a): reconciliation is idempotent ----------------------
#
# reconcile_scan may legitimately be re-invoked for the same scan_run (a retried
# job, a resumed CLI run, an operator re-running the step). Recording a second
# change_event for the same transition would inflate the history a human reads
# and corrupt "modified since" reasoning. The guard makes re-invocation a no-op.


@skip_without_db
def test_reconciling_the_same_scan_twice_records_the_change_once(session: Session) -> None:
    source = _make_source(session, endpoint=ENDPOINT_A)

    scan = run_scan(source, _fetcher(ENDPOINT_A, _document(requires_card=False)), session)
    first = reconcile_scan(scan, source, session)
    assert first.added == 1
    assert first.change_events == 1

    # Same scan_run, reconciled again: nothing new is recorded.
    second = reconcile_scan(scan, source, session)
    assert second.change_events == 0
    assert second.added == 0

    events = _change_events_for(session, _candidate_ids(session, scan.id))
    assert len(events) == 1, f"duplicate change events after re-reconciling: {len(events)}"


@skip_without_db
def test_reconciling_a_modification_twice_records_the_change_once(session: Session) -> None:
    source = _make_source(session, endpoint=ENDPOINT_A)

    first = run_scan(source, _fetcher(ENDPOINT_A, _document(requires_card=False)), session)
    reconcile_scan(first, source, session)

    second = run_scan(source, _fetcher(ENDPOINT_A, _document(requires_card=True)), session)
    assert reconcile_scan(second, source, session).modified == 1
    assert reconcile_scan(second, source, session).modified == 0

    events = _change_events_for(session, _candidate_ids(session, second.id))
    modifications = [e for e in events if e.change_type == "modified"]
    assert len(modifications) == 1


@skip_without_db
def test_reconciling_a_withdrawal_twice_records_the_change_once(session: Session) -> None:
    source = _make_source(session, endpoint=ENDPOINT_A)

    first = run_scan(
        source, _fetcher(ENDPOINT_A, _document(requires_card=False, extra_service=True)), session
    )
    reconcile_scan(first, source, session)

    # Gadgets vanishes.
    second = run_scan(source, _fetcher(ENDPOINT_A, _document(requires_card=False)), session)
    assert reconcile_scan(second, source, session).withdrawn == 1
    assert reconcile_scan(second, source, session).withdrawn == 0

    withdrawals = list(
        session.execute(select(ChangeEvent).where(ChangeEvent.change_type == "withdrawn")).scalars()
    )
    assert len(withdrawals) == 1


def _duplicate_rows(session: Session, scan_run_id: int) -> list[Candidate]:
    rows = list(
        session.execute(
            select(Candidate).where(Candidate.scan_run_id == scan_run_id).order_by(Candidate.id)
        ).scalars()
    )
    keys = [row.candidate_key for row in rows]
    return [row for row in rows if keys.count(row.candidate_key) > 1]


def _order_candidate_heap_by_descending_id(session: Session) -> None:
    """Rewrite the candidate heap into a known adversarial physical order.

    The index and ``CLUSTER`` rewrite are transactional in PostgreSQL. The test
    fixture rolls both back, so this establishes its own precondition without
    leaving schema or heap changes for another test module to repair.
    """

    index_name = f"ix_candidate_test_heap_{uuid4().hex}"
    session.execute(text(f"CREATE INDEX {index_name} ON candidate (id DESC)"))
    session.execute(text(f"CLUSTER candidate USING {index_name}"))
    session.expire_all()


def _heap_order(session: Session) -> list[int]:
    session.execute(
        text(
            "SET LOCAL enable_indexscan = off; "
            "SET LOCAL enable_indexonlyscan = off; "
            "SET LOCAL enable_bitmapscan = off"
        )
    )
    return [
        row_id
        for (row_id,) in session.execute(
            text("SELECT id FROM candidate"),
        ).all()
    ]


@skip_without_db
def test_an_identity_listed_twice_in_one_scan_is_withdrawn_exactly_once(session: Session) -> None:
    """One real-world withdrawal is one change event, even with duplicate rows.

    Nothing constrains ``(scan_run_id, candidate_key)`` to be unique, and
    ``run_scan`` legitimately persists one row per listing. A withdrawal guard
    keyed on the candidate *row* rather than on the identity lets a re-invocation
    withdraw the same identity a second time through the other row -- doubling
    the change history for a single real event. Reproduced end to end: the heap
    order is made adversarial between the two reconciliations.
    """

    source = _make_source(session, endpoint=ENDPOINT_A)

    first = run_scan(
        source,
        _fetcher(
            ENDPOINT_A,
            _document(requires_card=False, extra_service=True, duplicate_extra=True),
        ),
        session,
    )
    reconcile_scan(first, source, session)

    duplicates = _duplicate_rows(session, first.id)
    assert len(duplicates) == 2, "expected two candidate rows sharing one candidate_key"
    duplicate_key = duplicates[0].candidate_key

    # Gadgets vanishes from the document entirely.
    second = run_scan(source, _fetcher(ENDPOINT_A, _document(requires_card=False)), session)
    assert reconcile_scan(second, source, session).withdrawn == 1

    lowest_id = min(row.id for row in duplicates)
    _order_candidate_heap_by_descending_id(session)
    heap_order = _heap_order(session)
    assert heap_order == sorted(heap_order, reverse=True), (
        "expected descending-id CLUSTER to order the entire candidate heap"
    )
    duplicate_order = [row_id for row_id in heap_order if row_id in {row.id for row in duplicates}]
    assert duplicate_order[-1] == lowest_id, (
        "expected descending-id CLUSTER to put the lowest candidate id last"
    )

    assert reconcile_scan(second, source, session).withdrawn == 0

    prior = aliased(Candidate)
    withdrawals = list(
        session.execute(
            select(ChangeEvent)
            .join(prior, prior.id == ChangeEvent.previous_candidate_id)
            .where(
                ChangeEvent.change_type == "withdrawn",
                prior.candidate_key == duplicate_key,
            )
        ).scalars()
    )
    assert len(withdrawals) == 1, (
        "one identity withdrawn once must be one change event, not one per duplicate row: "
        f"{[(w.id, w.previous_candidate_id) for w in withdrawals]}"
    )


@skip_without_db
def test_the_withdrawal_loop_is_ordered_by_candidate_id(session: Session) -> None:
    """Which prior row a withdrawal cites must not depend on Postgres heap order."""

    source = _make_source(session, endpoint=ENDPOINT_A)

    first = run_scan(
        source,
        _fetcher(
            ENDPOINT_A,
            _document(requires_card=False, extra_service=True, duplicate_extra=True),
        ),
        session,
    )
    reconcile_scan(first, source, session)

    duplicates = _duplicate_rows(session, first.id)
    assert len(duplicates) == 2
    lowest_id = min(row.id for row in duplicates)

    # Rewrite the heap so an unordered scan would visit the higher id first.
    _order_candidate_heap_by_descending_id(session)
    heap_order = _heap_order(session)
    assert heap_order == sorted(heap_order, reverse=True), (
        "expected descending-id CLUSTER to order the entire candidate heap"
    )
    duplicate_order = [row_id for row_id in heap_order if row_id in {row.id for row in duplicates}]
    assert duplicate_order[-1] == lowest_id, (
        "expected descending-id CLUSTER to put the lowest candidate id last"
    )

    second = run_scan(source, _fetcher(ENDPOINT_A, _document(requires_card=False)), session)
    assert reconcile_scan(second, source, session).withdrawn == 1

    withdrawal = session.execute(
        select(ChangeEvent).where(ChangeEvent.change_type == "withdrawn")
    ).scalar_one()
    assert withdrawal.previous_candidate_id == lowest_id


@skip_without_db
def test_a_reappearance_in_an_unreconciled_scan_can_be_withdrawn_again(session: Session) -> None:
    """A genuine second withdrawal is recorded even across an unreconciled scan.

    ``run_provider_scans(..., reconcile=False)`` makes "scan without reconcile" a
    first-class mode, so a candidate can reappear in a scan that is never
    reconciled and then vanish again. That is two real withdrawals. This is a
    deliberate improvement on the previous behaviour, which keyed the guard on
    the identity's *last* change type and therefore silently swallowed the second
    withdrawal; the assertion below pins the improvement so it cannot regress or
    drift back by accident.
    """

    source = _make_source(session, endpoint=ENDPOINT_A)
    present = _document(requires_card=False, extra_service=True)
    absent = _document(requires_card=False)

    scan_a = run_scan(source, _fetcher(ENDPOINT_A, present), session)
    reconcile_scan(scan_a, source, session)

    scan_b = run_scan(source, _fetcher(ENDPOINT_A, absent), session)
    assert reconcile_scan(scan_b, source, session).withdrawn == 1

    # Scanned, deliberately not reconciled.
    run_scan(source, _fetcher(ENDPOINT_A, present), session)

    scan_d = run_scan(source, _fetcher(ENDPOINT_A, absent), session)
    assert reconcile_scan(scan_d, source, session).withdrawn == 1

    withdrawals = list(
        session.execute(select(ChangeEvent).where(ChangeEvent.change_type == "withdrawn")).scalars()
    )
    assert len(withdrawals) == 2
    assert len({w.previous_candidate_id for w in withdrawals}) == 2

    # ...and re-invoking the last reconciliation still adds nothing.
    assert reconcile_scan(scan_d, source, session).withdrawn == 0


# --- Q7-A carry-over (b): the 'unknown' materiality path, end to end --------
#
# classify_materiality returns 'unknown' for a changed field that is in neither
# the material nor the cosmetic set. That path is only reachable with real
# adapters, whose fact schemas vary per provider profile (the reference-JSON
# adapter emits a fixed five-key schema and can never produce it). Here a real
# HTML profile -- registered through the F008 S3 seam, exactly as a provider
# slice would -- changes one per-limit column, and the classification is
# followed through scan -> reconcile -> publication gate on real rows.

_UNKNOWN_PROFILE_NAME = "reconcile_probe_limits"
_UNKNOWN_ENDPOINT = "https://provider-u.example/limits"


def _probe_html(cpu_time: str) -> bytes:
    return (
        "<html><body><table id='probe-free-tier'>"
        "<tr><th>Service</th><th>Offer type</th><th>Card required</th>"
        "<th>Paid dependencies</th><th>CPU time</th></tr>"
        "<tr><td>Probe Service</td><td>always_free</td><td>No</td>"
        f"<td>No</td><td>{cpu_time}</td></tr>"
        "</table></body></html>"
    ).encode()


@pytest.fixture
def unknown_materiality_profile() -> Iterator[str]:
    """Register a provider-style HTML profile emitting an unrecognised fact."""

    from app.ingest.adapters.html import HTML_EXTRACTION_PROFILES, HtmlColumn
    from app.ingest.adapters.html import HtmlExtractionProfile as Profile
    from app.ingest.adapters.profiles import register_html_profile

    register_html_profile(
        Profile(
            name=_UNKNOWN_PROFILE_NAME,
            table_id="probe-free-tier",
            columns={
                "service": HtmlColumn("service", "text"),
                "offer type": HtmlColumn("offer_type", "text"),
                "card required": HtmlColumn("requires_card", "bool"),
                "paid dependencies": HtmlColumn("has_paid_dependencies", "bool"),
                # Neither material nor cosmetic -> classify_materiality 'unknown'.
                "cpu time": HtmlColumn("cpu_time", "text"),
            },
            required_fields=("service", "offer_type"),
        )
    )
    try:
        yield _UNKNOWN_PROFILE_NAME
    finally:
        HTML_EXTRACTION_PROFILES.pop(_UNKNOWN_PROFILE_NAME, None)


def _html_fetcher(body: bytes) -> FixtureFetcher:
    return FixtureFetcher(
        {_UNKNOWN_ENDPOINT: (body, "text/html")},
        FetchPolicy(official_domains=("provider-u.example",)),
    )


@skip_without_db
def test_an_unrecognised_changed_field_is_classified_unknown_end_to_end(
    session: Session, unknown_materiality_profile: str
) -> None:
    provider = Provider(slug="probe-provider", name="Probe Provider", type="cloud")
    session.add(provider)
    session.flush()
    source = Source(
        provider_id=provider.id,
        adapter_type="html",
        parser_profile=unknown_materiality_profile,
        trust_level="official",
        official=True,
        endpoint=_UNKNOWN_ENDPOINT,
        enabled=True,
        schedule="daily",
    )
    session.add(source)
    session.flush()

    now = datetime(2026, 4, 1, 8, 0, tzinfo=UTC)
    first = run_scan(source, _html_fetcher(_probe_html("10 ms")), session)
    reconcile_scan(first, source, session, now=now)

    # Only cpu_time changes: identity (service + offer_type) is stable, and the
    # field is in neither the material nor the cosmetic set.
    second = run_scan(source, _html_fetcher(_probe_html("30 ms")), session)
    result = reconcile_scan(second, source, session, now=now)
    assert result.modified == 1

    events = [
        e
        for e in _change_events_for(session, _candidate_ids(session, second.id))
        if e.change_type == "modified"
    ]
    assert len(events) == 1
    event = events[0]
    assert event.materiality == "unknown", (
        "an unrecognised changed field must never be assumed cosmetic"
    )
    assert event.publication_status == "draft"

    # The gate runs, and the unknown-materiality change is NOT auto-promoted.
    publish_scan(session, second, source, PUBLISHING, now=now)
    session.refresh(event)
    assert event.publication_status == "draft"


@skip_without_db
def test_a_cosmetic_only_change_is_not_classified_unknown(
    session: Session, unknown_materiality_profile: str
) -> None:
    """The counterpart: 'unknown' is not a catch-all for every non-material change."""

    assert classify_materiality(["cpu_time"]) == "unknown"
    assert classify_materiality(["notes"]) == "non_material"
    assert classify_materiality(["requires_card"]) == "material"
    assert classify_materiality(["notes", "cpu_time"]) == "unknown"
