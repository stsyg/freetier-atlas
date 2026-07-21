# Data Model

## Core entities

### Provider

ID, slug, name, official domains, type, source health, completeness score, freshness score.

### Service

Provider, canonical name, category, description, official URL, managed/self-hosted, portability traits.

### Offer

Service, offer type, Z class, status, eligibility, commercial/personal conditions, card requirement, paid dependencies, dates, visibility, first-seen and last-verified timestamps.

### Quota

Metric, amount, unit, reset period, scope, region scope, hard/soft behaviour, exhaustion behaviour, retention/deletion/reclamation policy.

### RegionAvailability

Provider region code, free availability, residency, data-plane location, control-plane location, notes.

### Evidence

Source, offer version, official flag, URL, title, excerpt, hash, retrieval/effective dates, selector/location, snapshot.

### Snapshot

Source, compressed content location, MIME type, hash, fetched time, expiry.

### OfferVersion

Immutable material offer facts.

### ChangeEvent

Added, modified, withdrawn, or restored; previous/new version, materiality, dates, publication status.

### Source

Provider, adapter type, trust, official flag, endpoint, schedule, parser profile, enabled state, health.

### ScanRun

Source, timing, status, documents, candidates, changes, errors.

### ReviewItem

Reason, evidence conflict, candidate facts, recommended action, admin disposition.

## Enums

### Zero-cost classes

- `Z0_TRUE_FREE`
- `Z1_BILLING_EXPOSURE`
- `Z2_TEMPORARY_OR_CONDITIONAL`
- `Z3_SELF_HOSTED_BUILDING_BLOCK`
- `UNKNOWN`

### Offer types

- `always_free`
- `recurring_quota`
- `new_customer_credit`
- `trial`
- `startup_program`
- `student_program`
- `open_source_program`
- `hackathon_promotion`
- `personal_use_free`
- `self_hosted_open_source`
- `other`

### Exhaustion behaviours

- `hard_stop`
- `request_rejected`
- `throttled`
- `service_sleeps`
- `read_only`
- `deployment_blocked`
- `site_disabled_until_reset`
- `resource_reclaimed`
- `data_deleted`
- `automatic_billing`
- `manual_upgrade_required`
- `unknown`

## Confidence

Store a numeric score internally. Public labels are:

- Verified
- Verified with caveats
- Likely accurate
- Recently detected
- Conflicting sources
- Stale verification
- Withdrawn

## Implementation

The domain model above is implemented as SQLAlchemy 2.0 declarative models in
`apps/api/app/models/` and created by the Alembic migration
`migrations/versions/0003_domain_model.py` (revision `0003_domain_model`,
following the F002 baseline `0001`/`0002`).

- **Models.** `app/models/domain.py` defines the 13 entities on a shared
  `Base.metadata` (`app/models/base.py`) with a deterministic constraint/index
  naming convention. `app/models/vocab.py` holds the closed vocabularies
  (zero-cost classes, offer types, exhaustion behaviours, change types, and the
  smaller status/visibility/materiality vocabularies) as the single source of
  truth for the `CHECK` constraints, so the models and the migration cannot
  drift apart.
- **Migration.** `migrations/env.py` sets `target_metadata` to the domain
  metadata and scopes autogenerate/`compare_metadata` to the domain tables (the
  `0001`/`0002` infrastructure tables are left untouched). Apply with
  `alembic upgrade head`; roll back this slice with
  `alembic downgrade 0002_worker_queue`. The API container applies migrations on
  startup (`apps/api/entrypoint.sh`).
- **Immutable offer versions.** `offer_version` holds *immutable material offer
  facts*. The migration installs a `BEFORE UPDATE OR DELETE` trigger
  (`trg_offer_version_immutable`) that rejects any mutation of an existing row;
  new versions are appended via `INSERT`.
- **Evidence provenance.** `evidence` links a `source`, an `offer_version`, and
  a `snapshot` via mandatory (`NOT NULL`) foreign keys, so every stored fact is
  traceable to its origin.
- **Tests.** `tests/unit/test_domain_models.py` checks the metadata shape and
  vocabulary membership offline; `tests/integration/test_domain_migration.py`
  (run against a live PostgreSQL) verifies apply, model/migration drift,
  foreign-key and check-constraint enforcement, offer_version immutability,
  provenance queries, and a downgrade/re-apply round trip. `scripts/stack-smoke`
  asserts the domain tables and the immutability trigger exist on the running
  stack.

## Z0 classification engine

The Z0 classification engine (`apps/api/app/classify/`) is a **pure,
deterministic** function that maps an offer's material facts to an explainable
zero-cost class. It performs no I/O, no network access, and never infers a
result from missing data.

- **Input** (`OfferFacts`): `offer_type`, `requires_card` and
  `has_paid_dependencies` (tri-state `True`/`False`/`None` where `None` means
  *unknown*), the set of quota `exhaustion_behaviours`, and the optional
  `eligibility` / `available_from` / `available_until` window.
- **Output** (`ClassificationResult`): the assigned `zero_cost_class`, an
  ordered list of human-readable `reasons`, and, for any non-Z0 verdict, the
  `blocking_conditions` that prevented Z0.

### Decision gates (in precedence order)

1. **Z3 — self-hosted building block.** `self_hosted_open_source` offers are
   free software that require self-provided infrastructure; their nature is
   determined before the billing gate (composing them onto verified Z0 hosting
   is a separate, deferred step).
2. **Z1 — billing exposure.** A required payment card, paid dependencies, or a
   quota that triggers `automatic_billing` on exhaustion is a definite billing
   exposure and can **never** be Z0. A known billing exposure dominates even
   when another field is unknown.
3. **UNKNOWN.** Any unknown material condition — `requires_card` or
   `has_paid_dependencies` is `None`, a quota exhaustion behaviour is `unknown`
   or unrecognised, or there is no quota data at all — blocks Z0. Per the safety
   rule an unknown material condition yields `UNKNOWN` rather than being guessed
   into a more specific class, so this gate **precedes** the Z2 gate: a trial (or
   any temporary/conditional signal) whose card or quota data is unknown is
   `UNKNOWN`, not `Z2`.
4. **Z2 — temporary or conditional.** Trials, new-customer credits, bounded or
   expired availability windows, eligibility-gated programs (student, startup,
   hackathon, open-source), or a quota that requires a manual paid upgrade to
   continue. Reached only when every material condition is known.
5. **Z0 — true $0.** Only when every billing gate is explicitly clear *and*
   every quota exhaustion behaviour is a safe stop-type (`hard_stop`,
   `request_rejected`, `throttled`, `service_sleeps`, `read_only`,
   `deployment_blocked`, `site_disabled_until_reset`, `resource_reclaimed`,
   `data_deleted`).

### Safety invariant

**No unknown or contradictory material condition may ever yield Z0.** When the
engine cannot positively confirm every Z0 condition, it returns `UNKNOWN` (or a
more specific non-Z0 class) rather than guessing. This is the product's core
anti-false-claim safeguard.

### Usage

`classify(facts)` classifies a plain `OfferFacts` value. `classify_offer(offer)`
is a thin, **read-only** adapter that reads a persisted `Offer` and its latest
`OfferVersion`'s `Quota` rows (or an explicitly supplied version) and returns
the same result; it performs no database writes. The closed vocabularies are
imported from `app.models.vocab`, so the engine and the schema share a single
source of truth. `tests/unit/test_z0_classifier.py` is a comprehensive truth
table covering every gate, every exhaustion behaviour, boundary/contradictory
inputs, and the safety invariant.

## Source ingestion: safe fetch guard and adapter contract

The ingestion pipeline (`apps/api/app/ingest/`) is built on two foundations
introduced in F004 Slice 1: a **safe fetch guard** (the sole network seam) and
the **source-adapter contract**. Both are additive and pure/standard-library
only; no new runtime dependency is introduced and nothing here writes to the
database or publishes offers.

### Safe fetch guard (`app.ingest.fetch`)

Every adapter reaches the network only through a `Fetcher`. The guard splits
into pure, independently-testable policy functions over a thin I/O layer
(docs/SECURITY_PRIVACY_ABUSE.md "Source fetching"):

- **Scheme allowlist** — `check_scheme` permits only the configured schemes
  (default `{https}`).
- **Official-domain allowlist** — `check_host` accepts a host only if it equals
  or is a subdomain of a provider's `official_domains`, evaluated **before** any
  DNS resolution or socket use.
- **SSRF / private-network blocking** — `address_block_reason` rejects loopback,
  RFC1918 private ranges, link-local `169.254.0.0/16` (including the
  `169.254.169.254` cloud-metadata address) and IPv6 `fe80::/10`, ULA
  `fc00::/7`, the unspecified address, multicast/reserved ranges, and unmasks
  IPv4-mapped IPv6 so a private v4 cannot be smuggled.
- **MIME validation**, a **bounded redirect count**, and a **streamed max-size
  cap** (`validate_mime`, `check_redirect_budget`, `check_size`).

The typed `FetchResult` carries `content`, `mime`, `final_url`, a SHA-256
`content_hash`, `fetched_at`, and `status`. Transports:

- `OfflineFetcher` — the safe default; **never opens a socket** (always raises
  `NetworkDisabledError`).
- `LiveFetcher` — a stdlib `urllib` transport **disabled by default**; it must be
  constructed with `enable_network=True`. It re-runs the scheme, host-allowlist
  and SSRF checks on **every redirect hop**, streams the body with an early size
  abort, and enforces connect/read timeouts.
- `FixtureFetcher` — a deterministic, offline test transport that still applies
  the pure URL/MIME policy checks.

### Adapter contract (`app.ingest.base`)

`SourceAdapter` is an `abc.ABC` enforcing the seven methods from
docs/ARCHITECTURE.md — `discover`, `fetch`, `canonicalize`, `extract`,
`validate`, `evidence`, `health` — so a subclass missing any one cannot be
instantiated. Adapters are constructed with a `Fetcher` and never import an HTTP
client directly. They exchange typed carriers `SourceDocument`, `CandidateFacts`,
`EvidenceLocation`, and `AdapterHealth`, and produce **candidate facts only**:
`CandidateFacts.verification_state` is constrained to
`app.ingest.vocab.ADAPTER_ASSIGNABLE_STATES`, so an adapter can never mint a
`verified` fact. `app.ingest.vocab.VERIFICATION_STATES` is the closed
verification-state vocabulary. `app.ingest.reference.JsonOfferAdapter` is a
minimal reference JSON adapter that makes the contract concrete end-to-end
offline. `tests/unit/test_ingest_fetch.py` and
`tests/unit/test_ingest_contract.py` cover the guard and the contract; the one
live-transport test binds `127.0.0.1` and allowlists it for that test only, so
the suite performs no external network egress.
