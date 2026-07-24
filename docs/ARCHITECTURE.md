# Architecture

## Logical components

```text
Public static web application
  ├── Catalogue and comparison
  ├── Project adviser
  ├── Evidence and history
  ├── RSS links
  └── Browser-side ZIP generator
             │
             ▼
FastAPI application
  ├── Public catalogue API
  ├── Adviser API
  ├── Admin API
  ├── RSS generation
  └── Hosting-proof API
             │
             ▼
PostgreSQL
  ├── Providers and services
  ├── Offers and quotas
  ├── Regions and residency
  ├── Evidence and snapshots
  ├── Offer versions and changes
  ├── Scan jobs and source health
  └── Review queue
             ▲
             │
Workers and scheduler
  ├── MCP adapters
  ├── API adapters
  ├── RSS/changelog adapters
  ├── HTML/browser adapters
  ├── Extraction
  ├── Verification
  └── Publication
```

## Technology baseline

- Python 3.13+, FastAPI, Pydantic, SQLAlchemy, Alembic
- React with a static-build-capable framework
- PostgreSQL
- PostgreSQL-backed queue initially
- PostgreSQL full-text/trigram search; pgvector later if justified
- Official Python MCP SDK/client abstraction
- Docker Compose and multi-architecture images
- pytest, frontend unit/e2e tests, provider fixtures
- YAML validated by Pydantic/JSON Schema

## Source adapter contract

Every adapter implements:

- `discover()`
- `fetch()`
- `canonicalize()`
- `extract()`
- `validate()`
- `evidence()`
- `health()`

Adapters return source documents and candidate facts, never directly published offers.

## Verification states

`detected`, `extracting`, `candidate`, `verified`, `verified_with_caveats`, `conflict`, `stale`, `withdrawn`, `rejected`.

## Publication gate

Automatic publication requires:

- approved official source
- schema-complete candidate
- deterministic parsing of material numbers
- reproducible fetch/extraction
- evidence for material claims
- no blocking contradiction
- sufficient confidence
- source freshness within policy

The gate is implemented in `apps/api/app/publish/` (F005): `revalidate.py`
deterministically re-derives the material numbers from the persisted facts,
`confidence.py` scores the signals above (weighted, deterministic) plus
completeness/freshness, and `gate.py` routes each candidate to **publish**
(all hard conditions met and confidence at/above the automatic threshold),
**review** (uncertain or contradictory evidence — a pending `review_item`,
never auto-published), or **withhold** (unofficial or unevidenced). On publish,
`publisher.py` upserts the `service`/`offer`, appends an **immutable**
`offer_version` (classified through the `classify_offer` Z0 bridge before
insert), writes its `quota` rows, links the official `evidence` to the new
version, and records a *published* `change_event`. Re-publishing identical
facts is idempotent (no new version); a material change appends a new version.
The confidence score and gate/classification reasons are stored inside the
version's `material_facts` JSONB. Publication is invoked from the ingest runner
(`run_provider_scans(..., publish=True)` / `python -m app.ingest.runner
--publish`); it is off by default. Only official, evidenced data can ever reach
`offer`/`offer_version`/`quota` — community data stays quarantined.

## Read-only catalogue API

The published catalogue is exposed over HTTP by the read-only catalogue API in
`apps/api/app/read_api/` (F005 slice 3), mounted under `/catalogue` and proxied
by the web nginx (`/api/catalogue/...`). It is strictly **read-only**: only
`GET` endpoints are registered, the injected DB session (`app.db.get_session`)
never commits (always rolls back), no LLM runs in the request path, and every
input is an internal identifier (a provider `slug` validated against a strict
pattern, or an integer offer id) — no endpoint accepts a URL/host or fetches
anything on the caller's behalf, so there is no SSRF surface. Queries
(`queries.py`) never touch the `candidate`/`discovery_candidate` tables and only
surface evidence linked to a published `offer_version`, so community /
pre-publication data can never leak. Serialization (`service.py`) reads the Z0
class + human-readable reasons, quotas, completeness/freshness signals, and the
confidence score back out of the version's `material_facts` JSONB. Per D039 the
primary confidence field is a plain-language **label** (`high`/`medium`/`low`,
or `unknown` when the score is absent — never guessed); the raw numeric score
and signals appear only inside an `advanced` detail block. The endpoints are:

- `GET /catalogue/providers` — providers list (summary + completeness/freshness)
- `GET /catalogue/providers/{slug}` — one provider with its metadata
- `GET /catalogue/providers/{slug}/category-states` — published offers grouped by
  category/service, each with its current Z0 state
- `GET /catalogue/providers/{slug}/offers` — a provider's published offers
- `GET /catalogue/offers/{id}` — offer detail: current version, Z0 class +
  reasons, quotas, confidence label (+ advanced numeric/signals),
  completeness/freshness
- `GET /catalogue/offers/{id}/evidence` — official evidence + provenance
  (source/snapshot) backing the current version + confidence label
- `GET /catalogue/offers/{id}/history` — append-only version history + published
  change events

### Catalogue query API (F006 slice 1)

The read API is extended (still strictly read-only, GET-only, same no-SSRF /
published-only / candidate-never-surfaced posture) with three catalogue-wide query
capabilities. Their query logic lives in `search.py` (deterministic search) and
`normalize.py` (shared, conservative quota-unit normalization); the canonical
category taxonomy is a code constant in `taxonomy.py` (no DB seed, no migration —
Alembic head stays 0007). The endpoints are:

- `GET /catalogue/search?q=&provider=&category=&zero_cost_class=&offer_type=&commercial_use=&status=&page=`
  — keyword search + composable filters over **published** offers. `q` is
  length-bounded and matched literally via parameterized `ILIKE` (its `LIKE`
  wildcards are escaped), so a hostile `q` (URL, SQL-ish, traversal) is neutralized
  rather than fetched or interpreted; every filter is validated against a closed
  set (slug pattern / enum vocabularies). Results are deterministically ordered by
  `(provider slug, service canonical name, offer id)` and paged with a fixed page
  size (owner decision Q3: in-DB match only; a full-text index is deferred to F008).
- `GET /catalogue/categories` — the canonical **14-category taxonomy × provider
  coverage** matrix. Every category is always present; each `(category, provider)`
  cell carries a closed-set coverage state derived strictly from published offers
  (`verified_free` when ≥1 published `Z0_TRUE_FREE` offer, `no_free_tier` when
  published offers exist but none are Z0, `not_offered` otherwise). A published
  service with no canonical category is not guessed into one — it is surfaced
  honestly in a per-provider `uncategorized` rollup.
- `GET /catalogue/compare?offers=1,2,3` — normalized side-by-side of a **bounded**
  set of published offers (id set validated + size-capped: oversize / non-integer →
  422, unknown/unpublished id → 404). Each quota amount is conservatively
  normalized (data sizes → bytes, keeping the SI/IEC decimal-vs-binary distinction;
  a small set of countable units passes through). Per owner decision Q7 anything
  that cannot be confidently normalized **fails closed** — it is reported as
  not-normalized with a note, never a guessed conversion — keeping "unknown is
  better than guessed" intact. The shared `normalize.py` helper is reused by the
  Slice 3 adviser.

Only Cloudflare is genuinely published today, so multi-provider search / matrix /
compare behaviour is proven with **clearly synthetic fixture** providers inserted
only inside rolled-back integration-test transactions (owner decision Q6); no
synthetic data is published on a normal stack run. Real cross-provider breadth
arrives in F008.

### Public web experience (F005 slice 4)

The `apps/web` single-page app renders a public, Cloudflare-focused provider page
that **consumes only this read API** over the same-origin `/api` proxy — it holds
no database connection, issues no writes, and adds no backend endpoint. Its
read-only client (`apps/web/src/api.ts`) issues plain `GET`s against fixed
`/api/catalogue/...` paths built solely from internal identifiers (a provider
slug, an integer offer id), so there is no user-controlled URL and no SSRF
surface. The page loads the provider detail, category-states, and offers, then
each offer's detail/evidence/history, and renders: category/service states with
zero-cost (Z0) badges; each offer's Z0 class with the plain-language reasons
behind it; the official evidence + provenance + link; the confidence **label** as
the primary signal (numeric score/signals only in an `advanced` disclosure, per
D039); version history + change events; completeness/freshness; and quota rows.
Consistent with "unknown is better than guessed", any `null`/absent value the API
returns is shown honestly as "Unknown". Accessibility is part of done: semantic
landmarks, a single `<h1>`, an accessible quota table, keyboard-operable
disclosures, and badges that pair colour with a text label + icon (never
colour-only). Catalogue-wide search and cross-provider comparison are now served
by the F006 query API above; surfacing them in the web UI, and the adviser, remain
deferred to later F006 slices.

## LLM routing

1. Deterministic parser/rules
2. Local model
3. Free hosted model with consent
4. Commercial model for configured escalation
5. Deterministic fallback

LLMs never receive provider credentials and never publish directly.

## Deployment profiles

Canonical Docker Compose: `web`, `api`, `worker`, `scheduler`, `postgres`.

The public Z0 deployment may separate static frontend, API, database, scheduler, and inference across verified Z0 providers. The exact dynamic-host choice requires real onboarding and quota tests.

## Retention

- Evidence excerpts, URLs, hashes, timestamps: indefinite
- Offer versions and change events: indefinite
- Raw compressed snapshots: 90 days
- Public project descriptions: not persisted
- Operational logs: short configurable retention with prompt/input exclusion
