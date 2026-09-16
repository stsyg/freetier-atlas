"""Integration tests for the outbound RSS change feed (F009-S4).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL. Each test runs
inside a rolled-back transaction that publishes the real Cloudflare catalogue,
then drives :func:`app.export.feed.build_change_feed` against the same
committed+in-transaction state the live routes read.

The populated developer database this may run against holds an unrelated corpus,
so every assertion is scoped to data this module controls (the Cloudflare offer
it publishes, or a change event it inserts) rather than to absolute totals.

Coverage maps to the mandatory invariants:

* Anti-drift parity: whether a feed item asserts "free" tracks the *live*
  ``/catalogue/offers/{id}`` handler's ``evidence_currency.current`` at the same
  clock -- the feed cannot become an eleventh surface that drifts from the others.
* The free-claim gate (boundary flip): the same offer exported one second either
  side of its real expiry boundary flips the affirmative free phrase in the XML
  itself -- staleness is baked in, not derived by the reader.
* Only already-published offers/events appear: an unpublished offer and a *draft*
  change event are both absent, each with a positive control.
* Deterministic: two builds at the same ``as_of`` are byte-identical.
"""

from __future__ import annotations

import importlib
import os
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config.loader import load_and_validate
from app.config.models import ProviderConfig
from app.export.feed import VERIFIED_FREE_PHRASE, build_change_feed
from app.ingest.reconcile import parse_schedule_window
from app.ingest.runner import build_fixture_fetcher, run_provider_scans
from app.models.domain import ChangeEvent, Offer
from app.read_api import queries
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

# ``app.read_api.router`` is the APIRouter instance; reach the real module (whose
# ``_now`` seam the parity test pins) explicitly.
router = importlib.import_module("app.read_api.router")

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "cloudflare.example.yaml"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ingest" / "cloudflare" / "html"
FREE = "Z0_TRUE_FREE"

#: A fixed, injected clock so the feed and the live handlers read the same now.
AS_OF = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
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


def _publish(session: Session) -> None:
    model = load_and_validate(str(CONFIG_PATH))
    config = model if isinstance(model, ProviderConfig) else ProviderConfig(**model)
    fetcher = build_fixture_fetcher(config, FIXTURES_DIR)
    run_provider_scans(session, config, fetcher, publish=True)
    session.flush()
    provider = queries.fetch_provider(session, "cloudflare")
    assert provider is not None, "cloudflare provider should exist after publish"


def _free_offer(session: Session):
    """A published, genuinely free, evidence-backed Cloudflare offer."""

    provider = queries.fetch_provider(session, "cloudflare")
    assert provider is not None, "cloudflare provider should exist after publish"
    for svc in provider.services:
        for offer in svc.offers:
            if not queries.is_published(offer) or offer.zero_cost_class != FREE:
                continue
            version = queries.latest_version(offer)
            if version is not None and version.evidence:
                return offer
    raise AssertionError("no published Z0_TRUE_FREE offer with evidence was produced")


def _pin_clock(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    monkeypatch.setattr(router, "_now", lambda: now)


def _guids(xml: str) -> set[str]:
    return {g.text for g in ET.fromstring(xml).findall("./channel/item/guid")}


def _description_for_guid(xml: str, guid: str) -> str:
    """The ``<description>`` text of the single item carrying ``guid``.

    Scoping to one item matters: the feed carries many offers, so a global
    substring search for the free phrase would match a *different* offer that is
    still current -- the assertion must be about THIS offer's item.
    """

    for item in ET.fromstring(xml).findall("./channel/item"):
        node = item.find("guid")
        if node is not None and node.text == guid:
            desc = item.find("description")
            return desc.text if desc is not None and desc.text is not None else ""
    raise AssertionError(f"no feed item carried guid {guid!r}")


def _items_for_offer(session: Session, offer_id: int) -> list[str]:
    """The guids the feed would carry for one offer's published change events."""

    events = queries.fetch_offer_change_events(session, offer_id=offer_id)
    return [f"urn:freetier-atlas:change:{e.id}" for e in events]


# --------------------------------------------------------------------------- #
# Anti-drift parity: the free assertion tracks the live handler's verdict      #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_feed_free_assertion_matches_live_offer_currency(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(session)
    _pin_clock(monkeypatch, AS_OF)
    offer = _free_offer(session)
    offer_id = offer.id

    # The published 'added' change event(s) for this offer surface as feed items.
    guids = _guids(build_change_feed(session, as_of=AS_OF))
    expected = _items_for_offer(session, offer_id)
    assert expected, "publishing must create at least one published change event"
    for guid in expected:
        assert guid in guids

    # Whether the item asserts "free" is exactly the live handler's verdict at the
    # same clock -- the feed reuses the ten-surface currency path, it does not
    # drift from it. Scoped to THIS offer's own item (other offers' currency is
    # irrelevant to the parity claim).
    live = router.get_offer(offer_id, session)
    xml = build_change_feed(session, as_of=AS_OF)
    item_desc = _description_for_guid(xml, expected[0])
    asserts_free = VERIFIED_FREE_PHRASE in item_desc
    assert asserts_free is (live.evidence_currency.current and live.zero_cost_class == FREE)


# --------------------------------------------------------------------------- #
# The free-claim gate: boundary flip in the exported XML itself                 #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_free_phrase_flips_across_the_real_expiry_boundary(session: Session) -> None:
    _publish(session)
    offer = _free_offer(session)
    version = queries.latest_version(offer)
    assert version is not None

    ages = [
        (e.snapshot.fetched_at, parse_schedule_window(e.source.schedule))
        for e in version.evidence
        if e.snapshot is not None and e.snapshot.fetched_at is not None
    ]
    assert ages, "the offer must rest on evidence with a real fetch time"
    fetched, window = min(ages, key=lambda pair: pair[0] + pair[1])

    events = queries.fetch_offer_change_events(session, offer_id=offer.id)
    guid = f"urn:freetier-atlas:change:{events[0].id}"

    at_boundary = build_change_feed(session, as_of=fetched + window)
    past_boundary = build_change_feed(session, as_of=fetched + window + timedelta(seconds=1))

    # Both builds carry the SAME item (same change event guid); the ONLY input
    # difference is one second of as_of.
    assert guid in _guids(at_boundary)
    assert guid in _guids(past_boundary)

    # age == window -> not stale -> free asserted; +1s -> stale -> no free claim.
    # Scoped to THIS offer's item, because other offers in the feed remain current
    # past this one's boundary and would keep the phrase present globally.
    assert VERIFIED_FREE_PHRASE in _description_for_guid(at_boundary, guid)
    assert VERIFIED_FREE_PHRASE not in _description_for_guid(past_boundary, guid)


# --------------------------------------------------------------------------- #
# Only already-published offers/events appear                                  #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_draft_change_event_is_absent_with_a_positive_control(session: Session) -> None:
    _publish(session)
    offer = _free_offer(session)
    version = queries.latest_version(offer)
    assert version is not None

    # Positive control: a published change event for this offer IS in the feed.
    published_guids = _items_for_offer(session, offer.id)
    assert published_guids
    before = _guids(build_change_feed(session, as_of=AS_OF))
    for guid in published_guids:
        assert guid in before

    # A DRAFT change event on the same published offer must never reach the feed:
    # a draft is a change a human has not released.
    draft = ChangeEvent(
        offer_id=offer.id,
        new_version_id=version.id,
        change_type="modified",
        materiality="material",
        publication_status="draft",
    )
    session.add(draft)
    session.flush()
    session.expire_all()

    after = _guids(build_change_feed(session, as_of=AS_OF))
    assert f"urn:freetier-atlas:change:{draft.id}" not in after
    # Positive control still holds.
    for guid in published_guids:
        assert guid in after


@skip_without_db
def test_unpublished_offer_contributes_no_items(session: Session) -> None:
    _publish(session)
    published = _free_offer(session)
    svc = published.service

    # An offer with no immutable version is unpublished; even if a change event
    # somehow references it, it must not surface.
    unpublished = Offer(
        service_id=svc.id,
        offer_type=published.offer_type,
        zero_cost_class=FREE,
    )
    session.add(unpublished)
    session.flush()
    orphan = ChangeEvent(
        offer_id=unpublished.id,
        change_type="added",
        materiality="material",
        publication_status="published",
    )
    session.add(orphan)
    session.flush()
    session.expire_all()
    assert queries.is_published(unpublished) is False

    guids = _guids(build_change_feed(session, as_of=AS_OF))
    assert f"urn:freetier-atlas:change:{orphan.id}" not in guids


# --------------------------------------------------------------------------- #
# Deterministic                                                                #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_feed_is_byte_identical_across_two_builds_at_same_as_of(session: Session) -> None:
    _publish(session)
    assert build_change_feed(session, as_of=AS_OF) == build_change_feed(session, as_of=AS_OF)
