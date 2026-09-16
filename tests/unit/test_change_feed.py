"""DB-free unit tests for the outbound RSS change feed (F009-S4).

These cover the pure surface of :mod:`app.export.feed`: the ``as_of`` stamping and
naive-``as_of`` guard, deterministic rendering and newest-first ordering, the
empty-feed first-run shape, stable guids, structural RSS 2.0 validity (parsed
back with the stdlib), and -- the invariant this slice exists for -- the
free-claim gate, proven by mutating one item's backing currency and watching the
affirmative free phrase disappear, with a fresh positive control so the assertion
cannot be a stuck instrument.

The full session-bound build, route/history currency parity, the real expiry
boundary flip and unpublished/draft absence are exercised against a real database
in ``tests/integration/test_change_feed.py``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import pytest
from app.export import feed
from app.export.feed import FeedItem, render_feed
from app.read_api import service
from app.read_api.currency import (
    ANCHOR_OFFER_VERSION,
    UNCHECKED,
    CurrencyContext,
    assess_currency,
)

from tests.support.synthetic import build_catalogue

_AS_OF = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Synthetic offer + currency helpers                                          #
# --------------------------------------------------------------------------- #


def _published_free_offer(*, evidence_age_days: int | None):
    """A single published Z0_TRUE_FREE offer whose currency the caller controls.

    ``evidence_age_days=None`` models the declaration-only shape (no checkable
    fetch time), which must resolve to *unchecked*, never current.
    """

    data = {
        "providers": [
            {
                "id": 1,
                "slug": "acme",
                "name": "Acme",
                "services": [
                    {
                        "id": 1,
                        "canonical_name": "Compute",
                        "offers": [
                            {
                                "id": 7,
                                "zero_cost_class": "Z0_TRUE_FREE",
                                "version": {
                                    "version_number": 1,
                                    "evidence_age_days": evidence_age_days,
                                    "evidence": [{"official": True, "url": "https://example/x"}],
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    offer = next(o for o in build_catalogue(data).offers if o.id == 7)
    offer.status = "published"
    return offer


def _detail(offer, currency: CurrencyContext):
    return service.serialize_offer_detail(offer, {}, currency)


def _fresh_currency(version_id: int) -> CurrencyContext:
    return CurrencyContext(
        index={(ANCHOR_OFFER_VERSION, version_id): assess_currency(_AS_OF, _AS_OF, "daily")},
        now=_AS_OF,
    )


def _stale_currency(version_id: int) -> CurrencyContext:
    old = _AS_OF - timedelta(days=90)
    return CurrencyContext(
        index={(ANCHOR_OFFER_VERSION, version_id): assess_currency(old, _AS_OF, "daily")},
        now=_AS_OF,
    )


def _unchecked_currency(version_id: int) -> CurrencyContext:
    return CurrencyContext(index={(ANCHOR_OFFER_VERSION, version_id): UNCHECKED}, now=_AS_OF)


def _item(offer, currency: CurrencyContext, *, change_id: int, change_type: str, occurred_at):
    return FeedItem(
        change_id=change_id,
        change_type=change_type,
        occurred_at=occurred_at,
        detail=_detail(offer, currency),
    )


# --------------------------------------------------------------------------- #
# as_of stamping and the naive-as_of guard                                    #
# --------------------------------------------------------------------------- #


def test_render_stamps_as_of_as_last_build_date() -> None:
    xml = render_feed([], as_of=_AS_OF)
    root = ET.fromstring(xml)
    last_build = root.find("./channel/lastBuildDate").text
    assert last_build == "Thu, 15 Jan 2026 12:00:00 GMT"


def test_render_rejects_naive_as_of() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        render_feed([], as_of=datetime(2026, 1, 15, 12, 0))


# --------------------------------------------------------------------------- #
# Empty feed (first run) is valid and carries zero items                      #
# --------------------------------------------------------------------------- #


def test_empty_feed_parses_and_has_no_items() -> None:
    root = ET.fromstring(render_feed([], as_of=_AS_OF))
    assert root.tag == "rss"
    assert root.attrib["version"] == "2.0"
    channel = root.find("channel")
    assert channel is not None
    assert channel.find("title") is not None
    assert channel.find("link").text == feed.PUBLIC_SITE
    assert channel.findall("item") == []


# --------------------------------------------------------------------------- #
# Deterministic rendering and newest-first ordering                           #
# --------------------------------------------------------------------------- #


def test_render_is_byte_identical_across_two_calls() -> None:
    offer = _published_free_offer(evidence_age_days=0)
    currency = _fresh_currency(offer.versions[0].id)
    items = [_item(offer, currency, change_id=1, change_type="added", occurred_at=_AS_OF)]
    assert render_feed(items, as_of=_AS_OF) == render_feed(items, as_of=_AS_OF)


def test_items_render_in_the_order_given() -> None:
    offer = _published_free_offer(evidence_age_days=0)
    currency = _fresh_currency(offer.versions[0].id)
    older = _AS_OF - timedelta(days=1)
    items = [
        _item(offer, currency, change_id=2, change_type="modified", occurred_at=_AS_OF),
        _item(offer, currency, change_id=1, change_type="added", occurred_at=older),
    ]
    root = ET.fromstring(render_feed(items, as_of=_AS_OF))
    guids = [g.text for g in root.findall("./channel/item/guid")]
    assert guids == [
        "urn:freetier-atlas:change:2",
        "urn:freetier-atlas:change:1",
    ]


# --------------------------------------------------------------------------- #
# Stable item identity                                                        #
# --------------------------------------------------------------------------- #


def test_guid_is_a_stable_urn_and_not_a_permalink() -> None:
    offer = _published_free_offer(evidence_age_days=0)
    currency = _fresh_currency(offer.versions[0].id)
    items = [_item(offer, currency, change_id=42, change_type="added", occurred_at=_AS_OF)]
    guid = ET.fromstring(render_feed(items, as_of=_AS_OF)).find("./channel/item/guid")
    assert guid.text == "urn:freetier-atlas:change:42"
    assert guid.attrib["isPermaLink"] == "false"


def test_item_carries_pubdate_and_category() -> None:
    offer = _published_free_offer(evidence_age_days=0)
    currency = _fresh_currency(offer.versions[0].id)
    items = [_item(offer, currency, change_id=1, change_type="withdrawn", occurred_at=_AS_OF)]
    item = ET.fromstring(render_feed(items, as_of=_AS_OF)).find("./channel/item")
    assert item.find("pubDate").text == "Thu, 15 Jan 2026 12:00:00 GMT"
    assert item.find("category").text == "withdrawn"


# --------------------------------------------------------------------------- #
# The free-claim gate (invariant): mutating currency removes the free phrase  #
# --------------------------------------------------------------------------- #


def _item_text(offer, currency: CurrencyContext) -> str:
    items = [_item(offer, currency, change_id=1, change_type="added", occurred_at=_AS_OF)]
    item = ET.fromstring(render_feed(items, as_of=_AS_OF)).find("./channel/item")
    return (item.find("title").text or "") + " " + (item.find("description").text or "")


def test_fresh_free_offer_asserts_free_positive_control() -> None:
    # Positive control for the two mutation tests below: with current evidence the
    # affirmative free phrase IS present. Without this, its absence below could be
    # a stuck instrument that never emits the phrase at all.
    offer = _published_free_offer(evidence_age_days=0)
    text = _item_text(offer, _fresh_currency(offer.versions[0].id))
    assert feed.VERIFIED_FREE_PHRASE in text


def test_stale_free_offer_makes_no_free_claim() -> None:
    # Same offer, same class, evidence now past its window: the item text must NOT
    # assert the offer is free. This is the eleventh-surface defect this slice
    # exists to prevent, tested by mutation rather than by prose.
    offer = _published_free_offer(evidence_age_days=0)
    fresh_text = _item_text(offer, _fresh_currency(offer.versions[0].id))
    stale_text = _item_text(offer, _stale_currency(offer.versions[0].id))
    assert feed.VERIFIED_FREE_PHRASE in fresh_text  # restored baseline
    assert feed.VERIFIED_FREE_PHRASE not in stale_text


def test_uncheckable_free_offer_makes_no_free_claim() -> None:
    # The "we could not look at all" arm: evidence with no fetch time is unchecked,
    # never current, so the item must not read as free (unknown stays unknown).
    offer = _published_free_offer(evidence_age_days=None)
    text = _item_text(offer, _unchecked_currency(offer.versions[0].id))
    assert feed.VERIFIED_FREE_PHRASE not in text


# --------------------------------------------------------------------------- #
# Structural RSS 2.0 validity (parsed back with the stdlib)                    #
# --------------------------------------------------------------------------- #


def test_feed_is_structurally_valid_rss_2_0() -> None:
    offer = _published_free_offer(evidence_age_days=0)
    currency = _fresh_currency(offer.versions[0].id)
    items = [_item(offer, currency, change_id=1, change_type="added", occurred_at=_AS_OF)]
    xml = render_feed(items, as_of=_AS_OF)

    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(xml)
    assert root.tag == "rss" and root.attrib["version"] == "2.0"
    channel = root.find("channel")
    # RSS 2.0 required channel children.
    for required in ("title", "link", "description"):
        assert channel.find(required) is not None
    item = channel.find("item")
    # RSS 2.0: an item needs at least a title or a description; ours carries both.
    assert item.find("title") is not None
    assert item.find("description") is not None
