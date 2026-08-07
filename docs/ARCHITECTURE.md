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
- schema-complete candidate, including an exact case-sensitive `offer_type`
  member from the canonical closed vocabulary
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
An invalid or non-string `offer_type` fails the `schema_complete` condition and,
when official evidence-backed, is routed to human review before any offer row is
resolved or inserted. The database CHECK remains a final defense, not the first
validator.
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
category taxonomy is a code constant in `taxonomy.py`. (F008 S1 later added migration
`0010_category_seed`, which seeds the DB `category` table from that same constant;
`taxonomy.py` remains the single source of truth. F008 S2 added
`0011_provider_category_coverage`.) The endpoints are:

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
  cell carries both the **declared** state read from `provider_category_coverage`
  and the **derived** state computed on demand by `read_api/coverage.py`
  (`derive_coverage_state()`), plus a `mismatch` flag when the two materially
  disagree. The closed set is the seven states in `models/vocab.py::COVERAGE_STATES`
  (`verified_free`, `offered_no_z0`, `not_offered`, `incomplete`, `stale`,
  `conflicting`, `unknown`). Since F008 S2 the endpoint **never infers coverage from
  a zero published-offer count**: an undeclared pair reports `unknown`, and
  `not_offered` appears only where a provider has explicitly declared it with a
  rationale. Derivation is pure and never persisted (decision Q11); a material
  declared-vs-derived contradiction is recorded durably as a pending `review_item`
  in the existing admin review queue. A published service with no canonical category
  is not guessed into one — it is surfaced honestly in a per-provider
  `uncategorized` rollup.
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

### Deterministic adviser core (F006 slice 3)

The adviser (`apps/api/app/adviser/`) turns a **strict, structured workload** into a
fully deterministic, evidence-backed $0 architecture recommendation. It is a *pure
function* of the request and the published catalogue: **no LLM sits in the path**
(the recommendation is produced with all providers disabled — the default — and the
corpus asserts exactly this), there is no natural-language parsing (that is F007),
and identical input always yields identical output. The pipeline is:

- `schema.py` — the only accepted input: a bounded (`extra="forbid"`) list of
  `Requirement`s, each in one of the 14 canonical categories with quantified
  `Demand`s (exact `Decimal` amounts) and `Constraints`. Every string field rejects
  URL/host/path markers, so the endpoint exposes no fetchable-URL / SSRF surface.
  Recommendation priorities are a product-fixed code constant (exactly $0 →
  portability → low lock-in), never caller input.
- `select.py` — reads only the **published** offer graph (the `candidate` /
  `discovery_candidate` quarantine tables are never queried) and partitions offers
  by zero-cost class. Z0-safety is enforced by re-running the shared `classify_offer`
  engine and comparing its verdict to the persisted `zero_cost_class`; only when they
  **agree** is the offer usable. A disagreement or an `UNKNOWN` verdict excludes the
  offer (fail closed). Only `Z0_TRUE_FREE` offers may enter a guaranteed-$0
  architecture; `Z3_SELF_HOSTED_BUILDING_BLOCK` is held for the self-hosting
  fallback; `Z1`/`Z2` are surfaced only in a separate "not $0" section.
- `quota_math.py` — exact-`Decimal` fit/headroom using the shared `normalize.py`
  Decimal path. A demand is covered only when a metric-matched, period-compatible
  quota normalizes into the same dimension with `headroom = quota − demand >= 0`; a
  boundary of exactly zero headroom fits. Any unknown/unnormalizable unit **fails
  closed** ("cannot guarantee", never guessed).
- `portability.py` — a deterministic portability score/label + lock-in label +
  exit-plan from a service's `deployment_model` and `portability_traits`;
  unrecognized traits are recorded but never scored ("unknown is better than
  guessed").
- `recommend.py` — the orchestrator. When a requirement has a fitting Z0 offer it
  picks the single best by a **stable total ordering** (most headroom margin →
  confidence label → portability → provider slug → offer id). When none fits it
  follows the strict impossible order: **(a) explain the blocking requirement →
  (b) reduction** (compute the exact reduced demand that fits the best available Z0
  headroom) **→ (c) recalculation** (re-run selection under the reduced demand) **→
  (d) self-hosting** (a Z3 building block placed on a Z0 host). `Z1`/`Z2` never enter
  the recommendation or the impossible order.
- `explain.py` + `schemas.py` — templated, evidence-backed explanations (quota math,
  Z0-safety reasons, portability, lock-in, exit-plan, and a whole-architecture "$0
  proof") assembled from persisted facts + `Evidence`, serialized with every
  fit-relevant amount as a `Decimal` **string** so no float round-trip can change a
  decision.

The endpoint is `POST /adviser/recommend` (`router.py`), mounted separately from the
GET-only `/catalogue`. It is **stateless**: a read-only DB session that never commits,
nothing persisted or logged, no LLM, no user-controlled URL, no DB writes. A JSON
eval corpus (`tests/fixtures/adviser/`) pins the deterministic output — satisfiable
single/multi-requirement architectures, exact quota-math boundaries, unknown-unit
fail-closed, the full impossible → reduction → recalculation → self-hosting order,
and Z1/Z2-only-in-the-separate-section — and a corpus runner asserts it with all LLM
providers disabled. Multi-provider / multi-option behaviour is proven with clearly
synthetic fixture offers (owner decision Q6), never published on a normal run. No new
runtime dependency (stdlib `decimal`) and no migration (Alembic head stays 0007).

### Deployment export (browser ZIP + non-persisted manifest) (F007 slice 3)

From a computed recommendation the product can produce a portable, self-hostable
**deployment scaffold** (a Docker Compose file, a placeholder-only `.env.example`,
a README, and a generation `MANIFEST.json`). The load-bearing rule
(docs/SECURITY_PRIVACY_ABUSE.md → "ZIPs: browser only") is that the server
**validates content but persists nothing** and the **browser assembles the
`.zip` client-side**.

- `apps/api/app/adviser/export.py` — a **pure** generator + fail-closed
  validators. `build_export(result)` recomputes nothing on disk: it produces the
  file *contents* in memory and returns an `ExportResponse` (`files:
  [{path, content, sha256, size}]` + a `manifest`). It never opens a file for
  writing and never touches the database. Every generated file is validated
  before return: **safe fixed paths** (allowlisted names, no traversal, no
  absolute paths, no backslashes), **text-only** (no NUL/control bytes), a
  **secret scan** (rejects AWS keys, credential-token prefixes, private-key
  blocks, and keyword-assigned non-placeholder values — `.env.example` carries
  placeholders only), a **Compose** that parses as YAML with a non-empty
  `services` map where **every** service declares a `healthcheck` and uses a
  **multi-arch** image (linux/amd64 **and** linux/arm64 asserted via
  `x-freetier-atlas.supported_platforms`), and a **total-size cap**. Any
  violation raises `ExportValidationError` (→ HTTP 422) and no content is echoed.
  Output is deterministic (no timestamps; sorted YAML/JSON) so identical input
  yields a byte-identical bundle.
- `POST /adviser/export` (`router.py`) reuses the **same** structured
  `RecommendationRequest` body (already rejecting URL-like input — no SSRF),
  recomputes the deterministic recommendation, and returns the validated bundle.
  Like `/adviser/recommend` it is **stateless**: a read-only DB session that
  never commits, nothing written to disk or DB, no LLM, no user-controlled URL.
- `apps/web/src/adviser/zip.ts` — a **dependency-free** STORE-method ZIP writer
  (manual CRC-32, UTF-8 filename flag, fixed DOS date for reproducibility) plus a
  guarded `downloadZip` trigger. No new npm dependency is added.
  `apps/web/src/adviser/DeploymentDownload.tsx` fetches the validated contents
  via `fetchDeploymentExport`, assembles the `.zip` entirely in the browser, and
  offers it as a download; it renders the manifest (files, sizes, asserted
  platforms, validation checks) verbatim and states up front that no secrets are
  included. No new migration (Alembic head stays 0007) and no new backend
  dependency (stdlib + already-present PyYAML).



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
colour-only). This single-provider page is retained at the `#/provider/cloudflare`
route inside the catalogue browser below.

### Catalogue browser (F006 slice 2)

The `apps/web` app grows from that single provider page into a
**provider-agnostic catalogue browser** — still consuming only the read API over
the same-origin `/api` proxy, with no database connection, no writes, and no new
backend endpoint. It is a small hash-routed SPA (no router dependency) with four
views: **Browse** (`#/`, keyword search + composable provider/category/
zero-cost-class/offer-type/commercial-use/status filters over
`/api/catalogue/search`, with a paged results list), **Categories**
(`#/categories`, the fourteen-category × provider coverage matrix from
`/api/catalogue/categories`), **Compare** (`#/compare`, a normalized side-by-side
of the two or three offers selected in Browse, from
`/api/catalogue/compare?offers=…`), and the retained Cloudflare provider page.
The read-only client (`apps/web/src/api.ts`) still issues plain `GET`s against
fixed `/api/catalogue/...` paths; filter values are appended only as query-string
parameters (internal slugs, closed enums, keywords, page number) via
`URLSearchParams`, so there remains no user-controlled URL and no SSRF surface.
The UI never re-derives a Z0 or confidence rating — the category-matrix coverage
`state`, `declared_state` and `derived_state` and every offer's classification come
verbatim from the API — the
confidence **label** stays the primary signal (numeric only inside a closed
advanced disclosure, per D039), and null fields render honestly as "Unknown"
(including quotas the API could not normalize, which are shown as reported and
labelled "normalized: Unknown" rather than converted by a guess). Accessibility
is asserted by the tests: one `<h1>` per route with ordered headings; `banner`/
`navigation`/`main`/`contentinfo` landmarks; an `aria-current` active nav link;
keyboard-operable form controls, checkboxes, and `<details>`; the matrix and
compare data tables carry a `<caption>` and `scope`d row/column headers; external
links use `rel="noopener noreferrer"`; and every badge pairs colour with a visible
label + an `aria-hidden` icon. Because only Cloudflare is really published, the
provider-agnostic rendering is proven in tests with **clearly synthetic** mocked
`fetch` responses carrying invented providers (owner decision Q6); the live stack
only ever shows real published data, so no false real-world free claim is emitted.
No new dependency is added (owner constraint Q8): interaction tests use
`fireEvent`. The adviser remains deferred to a later F006 slice.

### Adviser web experience (F006 slice 4)

The final F006 slice adds an **architecture adviser page** to the same `apps/web`
SPA at the new hash route `#/adviser`, consuming the deterministic adviser core
(F006 slice 3) over the same-origin `/api` proxy. The page presents an editable
**structured requirements form** (`apps/web/src/adviser/AdviserForm.tsx`): an
optional workload name plus one or more requirements, each in one of the fourteen
canonical categories, with repeatable quantified demands (metric + exact amount +
explicit unit + optional period) and optional constraints (commercial/personal
use, region, residency). It is deliberately a plain structured form — there is
**no natural-language input, no LLM, no consent flow, and no export** (all of that
is deferred to F007). Amounts are kept and emitted as strings so the backend
receives the exact `Decimal`. Submitting POSTs the typed `RecommendationRequest`
to `POST /api/adviser/recommend` through a single new fetcher
(`fetchRecommendation` in `apps/web/src/api.ts`); the request is a fixed
same-origin path with a structured body (never a user-controlled URL), the call
is stateless, and it is the only non-`GET` the SPA makes. `RecommendationView`
(`apps/web/src/adviser/RecommendationView.tsx`) renders the response **verbatim**:
the whole-architecture `$0` proof; per-component selected offer/provider, exact
per-demand quota math + headroom in an accessible table, Z0-safety reasons, and
portability/lock-in/exit-plan; the impossible-workload resolution in the strict
API order (1. blocking → 2. reduction → 3. recalculation → 4. self-hosting); and,
clearly separated, a "Not `$0` / paid" section for Z1/Z2 options that are never
mixed into the `$0` architecture. Fitting components still render even when the
workload is not fully `$0` (the orchestrator resolves requirements
independently). The UI never re-derives the Z0 class, confidence, or quota math —
it displays only what the API returns, the confidence **label** stays primary
(the numeric portability score sits only inside a closed advanced `<details>`,
per D039), and any null/absent field renders honestly as "Unknown". Accessibility
is asserted by the tests: a single `<h1>` for the route with ordered heading
levels down the deep recommendation tree (never skipping a level, never past
`h6`); landmarks and an `aria-current` nav link; keyboard-operable form controls
and disclosures; a `<caption>` + `scope`d headers on the quota-math table;
`rel="noopener noreferrer"` external evidence links; and every badge pairing
colour with a visible label + an `aria-hidden` icon. Provider-agnostic rendering
is proven with **clearly synthetic** multi-provider fixtures that live only in
`apps/web/src/adviser/testFixtures.ts`; the live stack shows only real published
Cloudflare data, so no false real-world free claim is emitted. No new npm or
Python dependency is added and the backend is untouched (Alembic head stays 0007).

## LLM routing

1. Deterministic parser/rules
2. Local model
3. Free hosted model with consent
4. Commercial model for configured escalation
5. Deterministic fallback

LLMs never receive provider credentials and never publish directly.

### LLM-assisted natural-language intake (F007 slice 1)

`POST /adviser/recommend/assisted` (`apps/api/app/adviser/router.py`) is the first
runtime consumer of `LlmSection`. It adds a **natural-language front door** to the
deterministic core without weakening any of its guarantees. The LLM's *only* job is
to turn a free-text description into a **candidate structured requirements dict**; the
routing ladder above (`apps/api/app/adviser/llm/routing.py`) resolves it:

1. **Deterministic parser** (`llm/parser.py`) — a conservative, rule-based reader with
   no LLM and no network. It emits at most one requirement per canonical category and
   only when a quantified demand appears in the same clause; otherwise it returns
   `None` (honest → fallback). It never guesses.
2. **Local model** (`ollama`, tier `LOCAL`) — no consent required.
3. **Free hosted model** (`gemini`, tier `FREE_HOSTED`) — external, **consent required**.
4. **Commercial model** (`openai`/`anthropic`, tier `COMMERCIAL`) — external, **consent required**.
5. **Deterministic fallback** — `interpretation=null`, `fallback_reason` set; the request
   never hard-fails.

Every candidate — parser- or LLM-produced — is validated through the **same strict**
`RecommendationRequest` schema (`extra=forbid`, bounds, `_reject_url_like`, exact-Decimal);
only a valid interpretation is fed to the existing `recommend()`. **Z0 / quota /
classification are never re-derived in the LLM path**, so a parser success is
**byte-identical** to `POST /adviser/recommend`. The LLM holds a single narrow
`interpret` capability (`llm/protocol.py`) — **no credentials, filesystem, shell,
URL-fetch, admin, or publication access**, and there is no LLM→publication path.

Providers are **disabled by default** and built from validated config
(`llm/runtime.py`, `LLM_CONFIG_PATH`); config load is **fail-safe** (a missing/invalid
file degrades to deterministic-only with safe default limits). A configured provider
`base_url` is checked by the shared egress guard (`llm/guards.py`, reusing
`app.ingest.fetch`). In CI and the live smoke the **only** adapter exercised is the
deterministic `FakeInterpreter` (`llm/fake.py`); the four real adapters
(`llm/adapters.py`) are thin, config-gated stubs that are never invoked by tests.

**Consent** is ephemeral: a per-request `{external_processing: bool}` assertion that is
**never persisted and never logged**, and re-asked each session. Without it, external
tiers are skipped (recording `consent_not_granted`) and the request takes the
local/deterministic path. The description itself is a transient prompt — never logged
or persisted.

The web SPA adds a **Structured | Describe-in-words** mode switch at `#/adviser`
(`apps/web/src/adviser/AssistedForm.tsx`): a bounded NL textarea plus a **consent
modal** (identifies the provider, warns against secrets/PII, explains external
processing, links the provider policy, and requires an explicit checkbox opt-in). The
result view shows the routing provenance (`llm_used`, `fallback_reason`, external-use
echo) and renders the deterministic recommendation through the existing
`RecommendationView`; when nothing could be interpreted it says so honestly and points
to the structured form. No migration is added (Alembic head stays **0007**).

## Deployment profiles

Canonical Docker Compose: `web`, `api`, `worker`, `scheduler`, `postgres`.

The public Z0 deployment may separate static frontend, API, database, scheduler, and inference across verified Z0 providers. The exact dynamic-host choice requires real onboarding and quota tests.

## Retention

- Evidence excerpts, URLs, hashes, timestamps: indefinite
- Offer versions and change events: indefinite
- Raw compressed snapshots: 90 days
- Public project descriptions: not persisted
- Operational logs: short configurable retention with prompt/input exclusion
