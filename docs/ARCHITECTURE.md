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
