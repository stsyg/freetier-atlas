# Catalogue static export (F009-S2, slice 1)

A deterministic, reproducible **build-time export** of the ten read-only
`/catalogue` GET routes into versioned JSON artefacts a static host can serve.

This exists because [ADR 0007](adr/0007-public-hosting-static-snapshot.md)
(Accepted) fixes the public deployment as a **static snapshot only**: there is no
live API in production, so the catalogue must be pre-rendered at build time.
Slice 2 builds the client-side search / filter / sort / compare experience and
the in-browser adviser on top of these files; this slice produces only the data.

## The governing risk

PRs #95 / #99 fixed a defect class where **nine catalogue surfaces each
independently repeated an expired free claim**. A snapshot freezes evidence
currency at build time, so this export is a **tenth surface** and the same defect
class applies to it by construction. Everything below is organised around making
the exported data *structurally incapable* of presenting a stale or
unknown-currency claim as current.

## Snapshot-age gating: the gate exists, but publication is still blocked (deployment blocker)

> **This artefact MUST NOT be published until a publisher consults the
> snapshot-age gate.** The gate now exists; nothing on the publication path reads
> it yet.

The five invariants below make every claim in the export honest **at build
time**. They do nothing about a snapshot built on Monday and still being served
three weeks later: every claim in that file still reads "current as of Monday",
and the reader has no way to know the *snapshot itself* went stale. This is the
same PR #95 / #99 defect class — an expired free claim shown as current — but
**displaced in time instead of across surfaces**, and it is the direction a
reader cannot see, which is what makes it serious.

**The blocker has changed in nature, not disappeared.** It was *"no gate
exists"*; a gate now exists (`apps/api/app/export/snapshot_age.py`, described
below). It is now *"a gate exists but nothing enforces it"*: the publication step
(F009-S6) that must call the gate and refuse a stale snapshot is unwritten, and
the gate's `loosest_window_exceeded` is deliberately a **signal, not a hardcoded
block** — F009-S6 owns publish policy. Publication therefore remains blocked
until that wiring exists, which is why this stays a deployment blocker.

> **Consumer hazard — read the gate, never the raw field.** Each artefact carries
> `evidence_currency.current`, and it is **correct only at `as_of`**. It is frozen
> at build time and says nothing about whether the claim is still current when the
> snapshot is *read* — which is the entire risk this section exists for. A consumer
> (F009-S6's publisher, slice 2's renderer) **must never read
> `evidence_currency.current` directly**: `app.export.snapshot_age.evaluate_export`
> is the **only sanctioned reader** of currency in a served snapshot, because it
> alone re-judges every claim against an injected read-time `now`. The raw field
> has the friendlier name and sits right next to the claim, so reaching for it is
> the obvious mistake — and it silently reintroduces exactly the expired-claim
> defect. The field is deliberately **not** renamed or removed: slice 1's
> route-parity test pins the artefact bytes against the live handlers, and breaking
> that anti-drift guarantee to solve a documentation problem is a bad trade. The
> discipline is the fix, not a change to the artefact shape.

The gate needs nothing the artefact does not already carry. Every serialised
`evidence_currency` block frozen into the export records, per claim, its
`oldest_fetched_at` and its own `window_days` (that claim's stalest source's
window). So the gate re-applies the *same* `age > window` rule the live currency
path uses — with `age = now - oldest_fetched_at` at read time instead of
`age = as_of - oldest_fetched_at` at build time — and needs no corpus, no
database, and no network. It lives in `apps/api/app/export/snapshot_age.py`
(`evaluate_export`), a pure function of `(rendered artefact map, injected now)`.

**The rule is per-claim, not global.** A claim degrades only when *its own*
backing source has exceeded *its own* window; because each claim carries its own
window, per-source discrimination is automatic — a stale hourly-backed claim
degrades while a fresh daily-backed claim in the same snapshot does not. A single
global figure is wrong in **both** directions: a global **tightest**-window rule
(the `2h` hourly `rss` source) would withhold nearly every claim two hours after
the build — a wrongly-withheld free offer, a defect of equal severity to a
wrongly-published one and in the same direction a reader cannot see — and a
global loosest-window rule would keep serving an already-stale tight claim.

There is exactly one honest **whole-snapshot** fact, and it is the *loosest*
window, not the tightest: once `now - as_of` exceeds
`max(manifest.staleness_windows)`, every checked claim is necessarily degraded
(each claim's window ≤ the loosest source window, and its total age exceeds
`now - as_of` which exceeds that window). The gate reports this as
`loosest_window_exceeded` — a coarse "everything is provably stale" signal for a
publisher's freshness policy, **not** a per-claim correctness threshold, and it
never wrongly withholds because below it per-claim gating still passes fresh
claims. The gate emits **nothing** into the artefact: a `now`-dependent verdict
baked at build time would itself go stale, which is the very bug being closed.

The gate returns a structured verdict; it does not rewrite the serializer's
words. A degraded (or unknown) claim carries a reason consistent with the change
feed's `_free_summary` — *"Free-tier status unverified: backing evidence is stale
or its currency is unknown"* — which a publication step (F009-S6) or a
client-side renderer (slice 2) surfaces. Neither consumer exists yet, so the gate
couples to neither.

This requirement is tracked as ledger item **`static-export-snapshot-age-gating`**,
which blocks any deployment of the export. It is repeated here because whoever
deploys this will be reading this document, not the ledger.

## What is exported

`python -m app.export --out DIR` writes, under `DIR`:

| Route | Artefact | Enumeration |
| --- | --- | --- |
| `/catalogue/providers` | `catalogue/providers.json` | all providers |
| `/catalogue/providers/{slug}` | `catalogue/providers/{slug}.json` | one per provider |
| `/catalogue/providers/{slug}/category-states` | `catalogue/providers/{slug}/category-states.json` | one per provider |
| `/catalogue/providers/{slug}/offers` | `catalogue/providers/{slug}/offers.json` | one per provider |
| `/catalogue/offers/{id}` | `catalogue/offers/{id}.json` | one per **published** offer |
| `/catalogue/offers/{id}/evidence` | `catalogue/offers/{id}/evidence.json` | one per published offer |
| `/catalogue/offers/{id}/history` | `catalogue/offers/{id}/history.json` | one per published offer |
| `/catalogue/search` | `catalogue/search.json` | full corpus (see below) |
| `/catalogue/categories` | `catalogue/categories.json` | single document |
| `/catalogue/compare` | `catalogue/compare.json` | full corpus (see below) |

Plus a top-level `manifest.json`.

Every artefact is a JSON **envelope**:

```json
{
  "as_of": "2026-06-01T12:00:00+00:00",
  "snapshot_version": 1,
  "generator": "freetier-atlas-catalogue-export/1",
  "route": "/catalogue/providers",
  "kind": "route",
  "data": { "...": "the exact body the live route returns" }
}
```

Corpus artefacts (`search.json`, `compare.json`) additionally carry
`"kind": "corpus"` and a `"note"` explaining what a static file can honestly
provide.

## Design decisions (the reviewer asked for these)

### Enumerating parameterised routes

`/providers/{slug}` and its two children are enumerated over
`queries.fetch_providers` (ordered by slug). `/offers/{id}` and its two children
are enumerated over every **published** offer (`queries.is_published`), sorted by
integer id. Each concrete instance is fetched through the *same* query helper the
live handler uses (`queries.fetch_provider`, `queries.fetch_offer`) so the
exported body is the byte-for-byte projection the route would return.

`manifest.json` lists the enumerated provider slugs and offer ids together with
`counts`. This doubles as a **positive control**: a broken enumerator returns an
empty list, which the manifest counts make visible, rather than a
plausible-looking wrong answer that silently ships.

### `/search` and `/compare` are queries, not resources

A static host cannot execute an arbitrary keyword search or an arbitrary
id-tuple comparison. Freezing an arbitrary subset would silently answer "no
result" for anything not pre-baked. Instead each is exported as the full
**corpus** it operates over, in the exact item shape the live route emits:

- `search.json` — every published offer as a search result item (the union of
  every page of an unfiltered search). Slice 2 runs keyword search, filtering and
  sorting over this corpus client-side.
- `compare.json` — every published offer's normalised compare cell, produced by
  the server-side `service.serialize_compare`, so the conservative quota
  normalisation is preserved and never re-implemented in the browser. Slice 2
  composes any comparison from these cells.

Critically, **each corpus item still carries its own `evidence_currency` verdict
frozen at `as_of`**, so a corpus item cannot present an expired claim as current
any more than a resource artefact can.

### Reusing the currency mechanism — NOT a tenth implementation

The export does **not** reimplement staleness. It calls the identical primitives
the nine live handlers call — `queries.currency_context` (which wraps
`app.read_api.currency`) and the `app.read_api.service` serializers — so the same
`confidence_label` collapse, `freshness -> null` withholding, and
`evidence_currency` block are baked into the artefacts.

One subtlety earns a single, whole-catalogue `CurrencyContext`: the live handlers
*scope* their context to the version ids a request touches, but that scope is an
optimisation only. A version's verdict is computed from its own evidence alone
(see `queries.fetch_evidence_currency`'s contract: "a narrowed call and a full
call agree on every key they share"), so the unscoped context yields
byte-identical verdicts for every version while letting the whole export share one
clock. The route-parity integration test pins this equivalence against the live
handlers.

### Per-source staleness windows

`manifest.json`'s `staleness_windows` reports **each source's** window, derived
via the same `app.ingest.reconcile.parse_schedule_window` the currency path uses
(PR #128 made windows a function of each source's cron cadence — 2× nominal, so
e.g. `2d` for daily sources, `2h` for the hourly RSS source). The export never
applies a single global figure.

## Invariants (acceptance criteria)

1. **Explicit `as_of`.** Every artefact and the manifest carry an injected
   `as_of`; it is never read from the wall clock inside the builder.
2. **Staleness is encoded in the data.** A stale/unknown claim serialises with a
   collapsed `confidence_label`, `freshness: null` (or a measured `0.0` for a
   checked-but-expired claim), and `evidence_currency.current = false`. A renderer
   cannot present it as fresh without re-deriving currency, which it need not and
   must not do.
3. **Unknown stays unknown.** Evidence that cannot be checked is
   `checked = false` (unchecked), never `current`, with `freshness: null`. An
   absent field is absent, not free.
4. **Per-source windows**, derived from each source's cadence (see above), never
   one global figure.
5. **Deterministic and reproducible.** `build_catalogue_export` is a pure function
   of `(database state, as_of)`; ordering is fixed and `canonical_json` emits
   sorted-key JSON, so the same state yields **byte-identical** output modulo the
   injected `as_of`. Verified by building twice and diffing.

## Usage

The wall clock is read at exactly one place — the CLI boundary — and only to
default `--as-of`. Pin `--as-of` to reproduce a published snapshot byte-for-byte.

```bash
# via the wrapper (resolves repo root, prefers .venv, sets PYTHONPATH).
# DATABASE_URL comes from your shell environment; see .env.example for the
# local-dev default.
scripts/export-catalogue.sh --out dist/catalogue --as-of 2026-06-01T12:00:00+00:00

# or directly (from apps/api on PYTHONPATH)
python -m app.export --out dist/catalogue --as-of 2026-06-01T12:00:00+00:00
```

```powershell
# DATABASE_URL comes from your shell environment; see .env.example for the local-dev default.
scripts/export-catalogue.ps1 --out dist/catalogue --as-of 2026-06-01T12:00:00+00:00
```

`--as-of` accepts ISO-8601; a naive value is read as UTC. The session is opened
read-only and rolled back — the export never writes to the database.

Reproducibility check:

```bash
scripts/export-catalogue.sh --out /tmp/a --as-of 2026-06-01T12:00:00+00:00
scripts/export-catalogue.sh --out /tmp/b --as-of 2026-06-01T12:00:00+00:00
diff -r /tmp/a /tmp/b   # empty: byte-identical
```

## Out of scope (slice 2)

Client-side search / filter / sort / compare UI and the in-browser deterministic
adviser. This slice touches nothing in `apps/web`.

Note that **publication itself is also out of scope**, and is *blocked*: see
[the snapshot-age gating blocker](#snapshot-age-gating-the-gate-exists-but-publication-is-still-blocked-deployment-blocker)
and ledger item `static-export-snapshot-age-gating`. The gate now exists, but no
publisher consults it yet, so this slice builds the artefact and must not ship it
until F009-S6 wires the gate into publication.
