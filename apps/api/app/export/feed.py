"""Deterministic build-time RSS feed of catalogue *changes* (F009-S4).

ADR 0007 (Accepted) fixes the public deployment as a *static snapshot only*, so
there is no live ``/feed`` route a server could hold open -- the feed is a
**build-time artefact**, produced by the same reproducible export path as the
catalogue JSON (:mod:`app.export.catalogue`) and sharing its one injected
``as_of`` clock.

This is the outbound feed. It is the opposite direction from the *inbound*
ingest adapter :mod:`app.ingest.adapters.rss`, which reads other people's feeds;
the two must not be confused.

What a "change" is, and why it is reproducible
==============================================
The feed is a pure projection of the append-only ``change_event`` ledger,
``publication_status = 'published'`` only -- the identical rows
``/catalogue/offers/{id}/history`` exposes via
:func:`queries.fetch_offer_change_events`. Each row is written by the publish
path (:mod:`app.publish.publisher`, ``added`` / ``modified``) or reconciliation
(:mod:`app.ingest.reconcile`, ``withdrawn``), carries a DB-stored ``occurred_at``
and an immutable DB-assigned ``id``, and is read at an **injected** ``as_of``
rather than the wall clock. Same database state + same ``as_of`` therefore yield
byte-identical XML (invariant: deterministic). The events are *not* recomputed by
diffing versions at build time, which would depend on when the build ran.

Feed window
===========
The feed is the most recent :data:`FEED_MAX_ITEMS` published change events, by
``(occurred_at, id)`` descending. A count bound (not a time bound) keeps the
artefact size stable and reproducible regardless of ``as_of``. On first run, or
whenever no change event has been published, the feed is a valid ``<channel>``
with zero ``<item>`` elements.

Stable item identity
=====================
Each item's ``<guid isPermaLink="false">`` is
``urn:freetier-atlas:change:{change_event.id}``. The id is an immutable primary
key, so the guid is stable across rebuilds and an aggregator never re-notifies a
reader of old news.

The free-claim gate (the reason this file is careful)
=====================================================
PRs #95 / #99 fixed a defect class where a surface repeated an expired free
claim; the catalogue export was the tenth surface and this feed is the eleventh.
A feed is worse than a page because items are cached and re-syndicated by readers
we do not control and cannot retract. So the feed does **not** reimplement
staleness: it acquires one :func:`queries.currency_context` at ``as_of`` (exactly
as the export does) and renders each item's offer state through the existing
``app.read_api.service`` serializer. An expired or unknown-currency claim arrives
already collapsed (``evidence_currency.current = false``,
``confidence_label = "unknown"``, ``freshness`` withheld).

Crucially, an item's **human-readable title and description assert "free" only
when** :attr:`evidence_currency.current` **is true** (see
:func:`_free_summary`) -- never from the raw ``zero_cost_class`` field, because
an aggregator re-syndicates the rendered words, not the structured verdict. A
mutation test flips the backing evidence past its window and asserts the affirmative
free phrase disappears from the same item.

A **withdrawal** is handled as its own direction (:func:`_item_summary`): a
withdrawn offer no longer exists, so its item asserts absence and **never** carries
a free claim, even though the still-published offer's ``OfferDetail`` may read as
free at ``as_of``. Announcing "still free" about an offer we have withdrawn, into a
cached and un-retractable item, is the worst output this feed could produce, so it
is structurally impossible rather than merely avoided.

Stdlib only
===========
The XML is generated with :mod:`xml.etree.ElementTree` and validated by parsing
it back in the tests. No feed-generation dependency is declared in any manifest
and none is added: a public-repo dependency expands the audit surface and the
lockfile for a format we emit in a few lines, and determinism is under our own
control here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime

from sqlalchemy.orm import Session

from app.read_api import queries, service
from app.read_api.schemas import OfferDetail

#: Bumped when the item/channel shape changes in a way a consumer must notice.
FEED_VERSION = 1
#: Stamped into the channel so a published feed is traceable to its producer.
GENERATOR = "freetier-atlas-change-feed/1"
#: The most recent N published change events the feed carries (a count bound, so
#: the artefact size is stable and reproducible regardless of ``as_of``).
FEED_MAX_ITEMS = 50
#: The canonical public site (ADR 0003/0007 primary host; on the URL allowlist).
PUBLIC_SITE = "https://freetier-atlas.pages.dev/"
#: The genuinely-free zero-cost class -- the only class a "free" phrase may claim.
_FREE_CLASS = "Z0_TRUE_FREE"
#: The affirmative free phrase. It appears in an item ONLY when the offer is
#: Z0 AND its evidence is current; the mutation test pins that it vanishes the
#: moment currency is lost.
VERIFIED_FREE_PHRASE = "Verified free"

#: Change types whose offer *currently exists* in the catalogue, so a present-
#: tense free claim is meaningful when evidence supports it. ``withdrawn`` is
#: deliberately absent: a withdrawn offer no longer exists, so asserting it is
#: "currently free" would be the single worst thing this feed could emit into a
#: reader it cannot retract. A withdrawal item therefore never renders a free
#: claim, regardless of the (still-present) offer's serialized detail.
_PRESENT_TENSE_CHANGES = frozenset({"added", "modified", "restored"})

#: A withdrawal item's body. It asserts absence, never a free-tier status, so no
#: aggregator can re-syndicate a "still free" claim about an offer we removed.
_WITHDRAWN_SUMMARY = "This offer has been withdrawn and is no longer listed in the catalogue."

_CHANNEL_TITLE = "FreeTier Atlas — catalogue changes"
_CHANNEL_DESCRIPTION = (
    "New, changed, and withdrawn zero-cost offers in the FreeTier Atlas "
    "catalogue. A build-time snapshot: each item's free-tier status is stated as "
    "of the feed's build date, never re-derived by the reader."
)

_NAIVE_AS_OF = "as_of must be timezone-aware; a feed's currency clock cannot be naive"

#: Neutral, currency-independent verbs. None asserts "free": an added offer whose
#: evidence is stale must not be announced as a free offer in its title.
_CHANGE_VERB = {
    "added": "Offer added",
    "modified": "Offer updated",
    "withdrawn": "Offer withdrawn",
    "restored": "Offer restored",
}


@dataclass(frozen=True)
class FeedItem:
    """One feed entry: a published change plus the offer's gated current state.

    ``detail`` is the exact :class:`OfferDetail` the live ``/catalogue/offers``
    handler would return at ``as_of``, so every currency verdict and collapsed
    label is inherited, never recomputed.
    """

    change_id: int
    change_type: str
    occurred_at: datetime | None
    detail: OfferDetail


def _rfc822(moment: datetime) -> str:
    """RFC-822 date text for RSS, rendered from the value alone (deterministic)."""

    return format_datetime(moment.astimezone(UTC), usegmt=True)


def _free_summary(detail: OfferDetail) -> str:
    """The free-tier sentence for an item, gated on evidence currency.

    The affirmative :data:`VERIFIED_FREE_PHRASE` is emitted ONLY when the offer is
    the genuinely-free class AND its evidence is current at ``as_of``. In every
    other case -- a non-free class, or a free class whose evidence is stale or
    unchecked -- no free claim is made; the collapsed ``confidence_label`` (which
    is ``"unknown"`` exactly when currency is absent) is reported instead. This is
    the single place the feed decides whether words may say "free", and it reads
    only the gated serializer output.
    """

    currency = detail.evidence_currency
    if detail.zero_cost_class == _FREE_CLASS and currency.current:
        return f"{VERIFIED_FREE_PHRASE} (Z0_TRUE_FREE); evidence current at build time."
    if detail.zero_cost_class == _FREE_CLASS:
        return (
            "Free-tier status unverified at build time: backing evidence is stale "
            "or its currency is unknown."
        )
    return f"Zero-cost class {detail.zero_cost_class} (confidence: {detail.confidence_label})."


def _item_summary(item: FeedItem) -> str:
    """The body sentence for an item, dispatched on the *direction* of the change.

    A withdrawal is the asymmetric, dangerous direction: it means the offer no
    longer exists, so its body asserts absence and can never carry a free claim --
    even though the (still-published) offer's :class:`OfferDetail` may still read
    as free at ``as_of``. Present-tense changes (added / modified / restored)
    describe an offer that *does* exist, so they defer to the currency-gated
    :func:`_free_summary`.
    """

    if item.change_type not in _PRESENT_TENSE_CHANGES:
        return _WITHDRAWN_SUMMARY
    return _free_summary(item.detail)


def _item_title(item: FeedItem) -> str:
    verb = _CHANGE_VERB.get(item.change_type, "Offer changed")
    detail = item.detail
    return f"{verb} — {detail.provider_name}: {detail.service_name}"


def _item_description(item: FeedItem) -> str:
    return f"{_item_summary(item)} Change: {item.change_type}."


def _append_item(channel: ET.Element, item: FeedItem) -> None:
    element = ET.SubElement(channel, "item")
    ET.SubElement(element, "title").text = _item_title(item)
    ET.SubElement(element, "description").text = _item_description(item)
    guid = ET.SubElement(element, "guid", {"isPermaLink": "false"})
    guid.text = f"urn:freetier-atlas:change:{item.change_id}"
    if item.occurred_at is not None:
        ET.SubElement(element, "pubDate").text = _rfc822(item.occurred_at)
    ET.SubElement(element, "category").text = item.change_type


def render_feed(items: list[FeedItem], *, as_of: datetime) -> str:
    """Render feed ``items`` to an RSS 2.0 document (pure, database-free).

    ``as_of`` is stamped as ``lastBuildDate`` and MUST be timezone-aware. Items
    are emitted in the order given; :func:`build_change_feed` sorts them newest
    first. The output is deterministic: element and attribute order are fixed and
    every date is rendered from its own value, never the wall clock.
    """

    if as_of.tzinfo is None:
        raise ValueError(_NAIVE_AS_OF)

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = _CHANNEL_TITLE
    ET.SubElement(channel, "link").text = PUBLIC_SITE
    ET.SubElement(channel, "description").text = _CHANNEL_DESCRIPTION
    ET.SubElement(channel, "generator").text = GENERATOR
    ET.SubElement(channel, "lastBuildDate").text = _rfc822(as_of)
    for item in items:
        _append_item(channel, item)

    ET.indent(rss)
    body = ET.tostring(rss, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def _collect_items(session: Session, *, currency: queries.CurrencyContext) -> list[FeedItem]:
    """Every published change event on a published offer, newest first.

    Only offers that are published (:func:`queries.is_published`) contribute, and
    only ``publication_status='published'`` change events
    (:func:`queries.fetch_offer_change_events` enforces the latter), so an offer a
    human deliberately withheld -- or a draft change event -- can never appear.

    Materiality is deliberately **not** a filter. Every published change event
    reaches the feed regardless of ``materiality`` in
    ``{material, non_material, unknown}``. The feed's cardinal failure is a
    withdrawal (or other material change) that silently never reaches a subscriber
    inside an item nobody can retract; excluding ``non_material`` would make that
    safety rest on an upstream classifier, so a material change mislabelled
    ``non_material`` would vanish unretractably. Including everything is the
    conservative choice -- a noisier feed, never a silently missing change. See
    docs/CHANGE_FEED.md.
    """

    items: list[FeedItem] = []
    for provider in queries.fetch_providers(session):
        for svc in provider.services:
            for offer in svc.offers:
                if not queries.is_published(offer):
                    continue
                events = queries.fetch_offer_change_events(session, offer_id=offer.id)
                if not events:
                    continue
                loaded = queries.fetch_offer(session, offer.id)
                if loaded is None:
                    continue
                cat_map = queries.category_map(session, [loaded.service.category_id])
                detail = service.serialize_offer_detail(loaded, cat_map, currency)
                for event in events:
                    items.append(
                        FeedItem(
                            change_id=event.id,
                            change_type=event.change_type,
                            occurred_at=event.occurred_at,
                            detail=detail,
                        )
                    )

    # Newest first, with id as the unique tiebreaker so items with an identical
    # occurred_at have a total, reproducible order rather than one that depends on
    # database row delivery.
    def _sort_key(item: FeedItem) -> tuple[datetime, int]:
        return (item.occurred_at or datetime.min.replace(tzinfo=UTC), item.change_id)

    items.sort(key=_sort_key, reverse=True)
    return items[:FEED_MAX_ITEMS]


def build_change_feed(session: Session, *, as_of: datetime) -> str:
    """Build the RSS change feed as a pure function of ``(DB state, as_of)``.

    Reads only, never writes, and never consults the wall clock: one
    :func:`queries.currency_context` at ``as_of`` is shared by every item so two
    items can never be assessed against different moments. ``as_of`` MUST be
    timezone-aware.
    """

    if as_of.tzinfo is None:
        raise ValueError(_NAIVE_AS_OF)

    currency = queries.currency_context(session, now=as_of)
    items = _collect_items(session, currency=currency)
    return render_feed(items, as_of=as_of)


__all__ = [
    "FEED_MAX_ITEMS",
    "FEED_VERSION",
    "GENERATOR",
    "PUBLIC_SITE",
    "VERIFIED_FREE_PHRASE",
    "FeedItem",
    "build_change_feed",
    "render_feed",
]
