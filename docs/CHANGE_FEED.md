# Change feed — outbound RSS (F009-S4)

A deterministic, reproducible **build-time RSS 2.0 feed** of catalogue *changes*
(new, changed, and withdrawn zero-cost offers), written next to the catalogue
JSON export as `feed.xml`.

This exists because [ADR 0007](adr/0007-public-hosting-static-snapshot.md)
(Accepted) fixes the public deployment as a **static snapshot only**: there is no
live API in production, so there is no `/feed` or `/rss` route a server could
hold open. The feed is produced at build time by the **same** export path as the
catalogue JSON (`python -m app.export`), sharing its one injected `as_of` clock.

This is the **outbound** feed. It is the opposite direction from the *inbound*
ingest adapter `apps/api/app/ingest/adapters/rss.py`, which reads other people's
feeds. The two must not be confused.

## The governing risk

PRs #95 / #99 fixed a defect class where catalogue surfaces each independently
repeated an expired free claim. The catalogue static export
([CATALOGUE_EXPORT.md](CATALOGUE_EXPORT.md)) was the **tenth** such surface; this
feed is the **eleventh**. A feed is worse than a page in one specific way: **feed
items are cached, re-syndicated, and re-read by aggregators long after
generation, and cannot be retracted.** A wrong free claim in a page is corrected
by redeploying; a wrong free claim in a feed has already been pulled into readers
we do not control. Everything below is organised around making a stale or
unknown-currency claim *structurally incapable* of rendering as a live free
claim in a feed item.

## The risk this slice does NOT close: snapshot-age gating (deployment blocker)

> **This feed MUST NOT be published without snapshot-age gating.**

The invariants below make every item honest **at build time**. They do nothing
about a feed built on Monday and still being served three weeks later: every item
still reads "free as of Monday", and an aggregator that pulled it has no way to
know the *snapshot itself* went stale. This is the same PR #95 / #99 defect class
— an expired free claim shown as current — but **displaced in time instead of
across surfaces**, and it is the direction a reader cannot see. For a feed it is
strictly worse than for the JSON export, because the reader has *already
syndicated* the item and there is no redeploy that reaches it.

The feed inherits this constraint from the export unchanged. The gate MUST be
**per-source, or keyed to the tightest window in the manifest — never a single
global figure** (after PR #128 windows differ per source: `2d` for daily sources,
`2h` for the hourly `rss` source, i.e. 2× the declared cron cadence). A snapshot
older than its tightest window MUST NOT be served, and that includes `feed.xml`.

This requirement is tracked as ledger item **`static-export-snapshot-age-gating`**,
which blocks any deployment of the export **and this feed**. It is repeated here
because whoever deploys the snapshot will be reading this document. This slice
only *builds* `feed.xml`; nothing publishes it yet, and it must not ship un-gated.

## What is produced

`python -m app.export --out DIR` writes `feed.xml` at the top level of `DIR`,
alongside the catalogue JSON artefacts. It is a single RSS 2.0 document:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>FreeTier Atlas — catalogue changes</title>
    <link>https://freetier-atlas.pages.dev/</link>
    <description>…build-time snapshot; free-tier status stated as of the build date…</description>
    <generator>freetier-atlas-change-feed/1</generator>
    <lastBuildDate>Mon, 01 Jun 2026 12:00:00 GMT</lastBuildDate>
    <item>
      <title>Offer added — GitHub: GitHub Packages</title>
      <description>Verified free (Z0_TRUE_FREE); evidence current at build time. Change: added.</description>
      <guid isPermaLink="false">urn:freetier-atlas:change:30</guid>
      <pubDate>Mon, 01 Jun 2026 09:12:03 GMT</pubDate>
      <category>added</category>
    </item>
  </channel>
</rss>
```

## Design decisions (the five questions the reviewer asked)

### 1. What a "change" is, and why it is reproducible

The feed is a pure projection of the append-only `change_event` ledger,
`publication_status = 'published'` only — the identical rows
`/catalogue/offers/{id}/history` exposes via
`queries.fetch_offer_change_events`. Each row is written by the publish path
(`app.publish.publisher`, `added` / `modified`) or reconciliation
(`app.ingest.reconcile`, `withdrawn`), carries a DB-stored `occurred_at` and an
immutable DB-assigned `id`, and is read at an **injected** `as_of` rather than
the wall clock.

Changes are **not** recomputed by diffing versions at build time, which would
depend on when the build ran. Same database state + same `as_of` therefore yield
byte-identical XML.

### 2. Feed window

A **count** bound, not a time bound: the most recent `FEED_MAX_ITEMS` (50)
published change events, ordered `(occurred_at, id)` descending. A count bound
keeps the artefact size stable and reproducible regardless of `as_of`; a time
window ("last 30 days") would make the item set depend on the build date and
churn the file every build even with no new changes. On first run, or whenever no
change event has been published, the feed is a valid `<channel>` with **zero**
`<item>` elements.

### 3. Stable item identity

Each item's `<guid isPermaLink="false">` is
`urn:freetier-atlas:change:{change_event.id}`. The id is an immutable primary
key, so the guid is **stable across rebuilds**: an aggregator dedupes on it and
never re-notifies a reader of old news. `isPermaLink="false"` tells the reader the
guid is an opaque identity, not a fetchable URL (correct under a static host).

### 4. Library or stdlib — stdlib

The XML is generated with the standard library (`xml.etree.ElementTree`) and
validated by parsing it back in the tests. **No feed-generation dependency is
declared in any manifest and none is added.** A public-repo dependency expands
the audit surface and the lockfile; the format here is a few elements we emit in
a few lines, and determinism (fixed element/attribute order, dates rendered from
each value via `email.utils.format_datetime(..., usegmt=True)` in the C locale)
is under our own control. Adding a dependency would be a deliberate, approval-
gated decision, and there is no justification for one here.

### 5. Currency — reused, not reimplemented

The feed does **not** reimplement staleness. It acquires one
`queries.currency_context` at `as_of` (exactly as the export does) and renders
each item's offer state through the existing `app.read_api.service` serializer,
so the same `confidence_label` collapse, `freshness -> null` withholding, and
`evidence_currency` verdict are inherited. An expired or unknown-currency claim
arrives already collapsed (`evidence_currency.current = false`).

Crucially, an item's **human-readable title and description assert "free" only
when `evidence_currency.current` is true** (`feed._free_summary`) — never from
the raw `zero_cost_class` field, because an aggregator re-syndicates the rendered
words, not the structured verdict. Titles use neutral verbs ("Offer added",
"Offer updated", "Offer withdrawn", "Offer restored") that never assert free on
their own. The affirmative phrase `Verified free` appears in an item **iff** the
offer is `Z0_TRUE_FREE` **and** its evidence is current at `as_of`.

### Withdrawal is its own direction (and never says "free")

A withdrawal is the asymmetric, dangerous change: a missed *addition* is a missed
announcement, but a missed or mis-worded *withdrawal* leaves subscribers believing
a free offer still exists, inside cached items nobody can retract. A `withdrawn`
item is therefore dispatched separately (`feed._item_summary`): it asserts absence
("This offer has been withdrawn and is no longer listed in the catalogue.") and
**never** carries a free claim — even though the still-published offer's
`OfferDetail` may read as free and current at `as_of`. Announcing "still free"
about an offer we removed would be the worst output this feed could emit, so it is
structurally impossible, not merely avoided. An end-to-end integration test
synthesises a *published* withdrawal on a currently-free offer and asserts the item
reaches the feed with the withdrawal category and no free phrase, against a
positive control that the same offer's `added` item does assert free.

### Materiality is not a filter (deliberately)

Every published change event reaches the feed regardless of its
`materiality ∈ {material, non_material, unknown}`. We include everything on
purpose. Excluding `non_material` would make the feed quieter, but its safety
would then rest entirely on an upstream classifier: a genuinely material change
mislabelled `non_material` would vanish from the feed silently and
unretractably — the exact failure the feed exists to prevent. A noisier feed is
the conservative choice; a silently missing withdrawal is not acceptable. (The
current corpus is 100% `material`, so a materiality filter would never surface in
testing and would first surface in production — another reason to decide it here,
explicitly, rather than leave it to a default.)

## Invariants (acceptance criteria)

1. **No expired or unknown-currency free claim** may appear in any item. The
   affirmative free phrase is gated on `evidence_currency.current`; a boundary
   test builds the same offer one second either side of its real expiry and
   asserts the phrase flips in the XML. A **withdrawal** item never asserts free
   at all (see above), proven end-to-end against a published withdrawal of a
   currently-free offer.
2. **Unknown stays unknown.** An offer whose evidence cannot be checked serialises
   as not-current; the item makes no free claim. An absent field never renders as
   a free claim.
3. **Deterministic and reproducible.** `build_change_feed` is a pure function of
   `(database state, as_of)`; ordering and attribute order are fixed and dates are
   rendered from their own values, so the same state yields **byte-identical**
   output. Verified by building twice and diffing.
4. **Only already-published offers and events appear.** Only offers with an
   immutable version (`queries.is_published`) and only
   `publication_status='published'` change events contribute, so an offer or a
   change a human deliberately withheld can never appear. Verified by an absence
   test with a positive control.
5. **Valid feed output.** RSS 2.0. Every test parses the output back with
   `xml.etree.ElementTree` and asserts structure rather than matching strings.

## Usage

The feed shares the export CLI; `DATABASE_URL` comes from your shell environment
(see `.env.example` for the local-dev default).

```bash
# writes catalogue JSON + feed.xml under dist/catalogue
python -m app.export --out dist/catalogue --as-of 2026-06-01T12:00:00+00:00
```

`--as-of` accepts ISO-8601; a naive value is read as UTC. Pin it to reproduce a
published `feed.xml` byte-for-byte. The session is opened read-only and rolled
back — the feed never writes to the database.

Reproducibility check:

```bash
python -m app.export --out /tmp/a --as-of 2026-06-01T12:00:00+00:00
python -m app.export --out /tmp/b --as-of 2026-06-01T12:00:00+00:00
diff /tmp/a/feed.xml /tmp/b/feed.xml   # empty: byte-identical
```

## Out of scope

Publication itself, which is *gated*: see
[the snapshot-age gating blocker](#the-risk-this-slice-does-not-close-snapshot-age-gating-deployment-blocker)
and ledger item `static-export-snapshot-age-gating`. This slice builds
`feed.xml`; it does not — and must not — ship it un-gated. This slice touches
nothing in `apps/web`.
