# Data Model

## Core entities

### Provider

ID, slug, name, official domains, type, source health, completeness score, freshness score.

### Service

Provider, canonical name, category, description, official URL, managed/self-hosted, portability traits.

`service.category_id` is a **nullable, mutable** FK to `category` with `ON DELETE SET NULL`. It is
declared metadata, never inferred: a service whose category is not declared in its provider config
(`service_categories`) stays `NULL` and is reported honestly as uncategorised. Category is *not* a
material fact — it is absent from `publish/publisher._stable_material_facts()`, so setting or
changing it never alters an offer's `content_hash` and never mints a new `offer_version`. This is
what makes back-filling categories over an already-published catalogue safe.

### Category

The fourteen canonical product categories (`docs/PRODUCT_REQUIREMENTS.md`, decision D025). The
single source of truth for the slug/name/ordinal set is `apps/api/app/read_api/taxonomy.py`
(`CATEGORY_TAXONOMY`); migration `0010_category_seed` seeds those exact fourteen rows and asserts
set equality with the constant. The seed is idempotent (`ON CONFLICT (slug) DO NOTHING`) and its
downgrade deletes only those fourteen slugs, degrading categorised services to `category_id IS NULL`
rather than failing on the FK.

### ProviderCategoryCoverage

The **declared** coverage state of one `(provider, category)` pair — "does this provider offer
anything in this category, and if so is it Z0?". Columns: `provider_id` (FK, `ON DELETE CASCADE`),
`category_id` (FK, `ON DELETE CASCADE`), `state`, `rationale`, `source_id`
(FK → `source`, `ON DELETE SET NULL`), `evidence_url`, `declared_at`, `created_at`, with
`UNIQUE(provider_id, category_id)`. Introduced by migration `0011_provider_category_coverage`.

The vocabulary is the seven-member closed set in `apps/api/app/models/vocab.py::COVERAGE_STATES`:
`verified_free`, `offered_no_z0`, `not_offered`, `incomplete`, `stale`, `conflicting`, `unknown`.

Three honesty rules are enforced by **database CHECK constraints**, not only in Pydantic, so a raw
`INSERT` cannot bypass them:

- `state` must be one of the seven;
- `state = 'not_offered'` requires a non-empty `rationale` — a claim that a provider deliberately
  does not offer a category is a claim, and must be justified;
- `state IN ('verified_free', 'offered_no_z0')` requires `source_id IS NOT NULL OR evidence_url IS
  NOT NULL` — an offer claim must be provenance-backed.

The same expressions back the ORM `CheckConstraint`s, so the migration and the model cannot drift.

**This table stores the declaration only.** There is deliberately **no `derived_state` /
`derived_at` column** (decision Q11). The *observed* state is computed on demand by the pure
`apps/api/app/read_api/coverage.py::derive_coverage_state()` from published offers, pending
evidence-contradiction review items and staleness, and is never persisted or cached — a stored
projection would be a second source of truth that can silently go stale, which is exactly what the
`stale` state exists to detect. `derive_coverage_state()` **never returns `not_offered`**: that
state is only ever a declaration. Should a read-path performance problem ever appear, the sanctioned
escape hatch is a read-only SQL *view*, never a written column.

The durable audit artefact for a material declared-vs-derived contradiction is the existing
`ReviewItem` (`reason='evidence_conflict:coverage:<provider>:<category>'`,
`recommended_action='manual_review'`, `admin_disposition='pending'`), raised by
`app.ingest.reconcile_coverage.reconcile_coverage()` and surfaced through the existing F007
`GET /api/admin/review-queue`. No new admin surface exists for coverage.

Declarations are **withdrawable**: `ingest.config_sync.sync_coverage()` upserts on
`(provider_id, category_id)`, overwrites a changed state (refreshing `declared_at` only when the
content actually changes) and deletes rows for pairs the provider YAML no longer declares, so the DB
converges on the declared truth rather than retaining a stale row. The same principle now applies to
`service_categories`: withdrawing a service's declaration reverts `service.category_id` to `NULL`.

**A resolution failure is not a withdrawal** — on either axis, though the two are protected by
different mechanisms because they fail differently:

- An unresolvable **`source`** reference still resolves its category, so the pair is registered as
  declared and its stored row is left exactly as it stands.
- An unresolvable **category** slug (taxonomy drift, typically a rename — note the FK's
  `ON DELETE CASCADE` does *not* help here, because the category row still exists) leaves the sync
  unable to name the id its stored row is keyed on. That row becomes indistinguishable from a
  genuinely withdrawn pair, so withdrawal cannot be positively proven and the prune is suppressed
  **wholesale for that run** (`CoverageSyncResult.prune_suppressed`) rather than deleting what it
  cannot attribute.

Both conditions surface in the `unknown_source` / `unknown_category` outcome counts. Only a pair the
YAML genuinely stops declaring, in a run where every category reference resolved, is pruned. The conservative
error is retaining a row that could have been withdrawn, which is the correct side to err on.

The Q9-A evidence floor is enforced **twice** — once at config load
(`ProviderConfig.validate_coverage_floor()`) and once after every sync against the rows actually
persisted. `sync_coverage()` re-reads the provider's stored rows and raises `CoverageFloorError` if
fewer than three are evidence-backed `verified_free` / `offered_no_z0`. Zero persisted rows is not an
exemption but the maximal erosion, and is reported as such; the sole skip is a database with no
canonical taxonomy at all (pre-0010), where the sync writes nothing. The floor is therefore a
**database** invariant, not only a config-load one.

One condition is worth stating precisely rather than claiming more than holds: the erosion is
prevented from committing because the exception propagates out of the caller's transaction, and every
current caller lets it. That is a property of the callers, not yet of `sync_coverage` itself — a
caller that caught the error and committed anyway would persist the shortfall, since the writes are
already flushed. Making the guarantee structural (a provider-unit savepoint) is tracked as a
follow-up.

### Offer

Service, offer type, Z class, status, eligibility, commercial/personal conditions, card requirement, paid dependencies, dates, visibility, first-seen and last-verified timestamps.

### Quota

Metric, amount, unit, reset period, scope, region scope, hard/soft behaviour, exhaustion behaviour, retention/deletion/reclamation policy.

### RegionAvailability

Provider region code, free availability, residency, data-plane location, control-plane location, notes.

### Evidence

Source, offer version *or* candidate (pre-publication), official flag, URL, title, excerpt, hash, retrieval/effective dates, selector/location, snapshot. `source` and `snapshot` are always mandatory; a `CHECK` requires at least one subject link (`offer_version_id` or `candidate_id`).

### Snapshot

Source, compressed content location, MIME type, hash, fetched time, expiry.

### OfferVersion

Immutable material offer facts.

### ChangeEvent

Added, modified, withdrawn, or restored; links either a previous/new
`offer_version` (published history) or a previous/new `candidate`
(pre-publication reconciliation diff); materiality, dates, publication status. A
`CHECK` requires at least one linkage target so a change event is never orphaned.

### Source

Provider, adapter type, trust, official flag, endpoint, schedule, parser profile, enabled state, health.

### ScanRun

Source, timing, status, documents, candidates, changes, errors.

### ReviewItem

Reason, evidence conflict, candidate facts, recommended action, admin disposition.

### Candidate

A pre-publication observation produced by a `ScanRun`: scan run, source, optional
service/offer links, verification state, extracted `candidate_facts` (JSONB), a
deterministic `content_hash` of those facts, a stable `candidate_key` (identity
for cross-scan change detection), an `official` flag, and timestamps. A candidate
never links to an `offer_version` and is never born `verified`. Only candidates
from official sources receive `evidence`.

### DiscoveryCandidate

A quarantined, community-provenance discovery record: source, repository, url,
licence, discovery date, import method, verification status, candidate/official
names, notes. It has **no** foreign key to `evidence` or `offer_version` — a
community source can surface a coverage gap but can never establish a fact.

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

- **Models.** `app/models/domain.py` defines the 15 entities on a shared
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
- **Evidence provenance.** `evidence` links a `source` and a `snapshot` via
  mandatory (`NOT NULL`) foreign keys, so every stored fact is traceable to its
  origin. Its *subject* is either a published `offer_version` (F003) or a
  pre-publication `candidate` (F004); both links are individually optional but a
  `CHECK` (`ck_evidence_evidence_link_target`) requires at least one, so evidence
  is never orphaned.
- **Ingestion persistence (migration `0004_ingest_candidates`).** Adds the
  `candidate` and `discovery_candidate` tables and relaxes `evidence` (adds the
  optional `candidate_id` link, makes `offer_version_id` nullable, adds the
  link-target `CHECK`). `app.ingest.scan.run_scan(source, fetcher, session)`
  orchestrates one scan — fetch → canonicalize → extract → validate → persist a
  hashed `Snapshot`, `Candidate` rows, and (for `trust_level == "official"`
  sources only) `Evidence`; community sources write only `discovery_candidate`
  and never `evidence`. There is **no publication path**: `run_scan` never
  creates or mutates `offer`/`offer_version` and writes no `change_event` rows,
  so the offer-version immutability trigger is untouched. Content hashing is
  deterministic, so re-scanning identical input yields identical candidate hashes
  and detects zero changes. The migration is reversible (`alembic downgrade
  0003_domain_model`).
- **Reconciliation (migration `0005_change_event_candidate_link`).** Relaxes
  `change_event` so the reconciliation pass can record *pre-publication*
  candidate diffs: adds the optional `previous_candidate_id` / `new_candidate_id`
  links to `candidate`, makes `offer_id` nullable, and adds the link-target
  `CHECK` (`ck_change_event_change_link_target`). `app.ingest.reconcile.reconcile_scan(scan_run, source, session)`
  runs *after* `run_scan` (never wired into it, so scanning still writes zero
  change events) and operates on candidate content only. It (1) diffs each
  freshly-scanned candidate against the last-known candidate for the same source
  + identity and emits a DRAFT `change_event` with a deterministic `change_type`
  (`added`/`modified`/`withdrawn`/`restored`) and `materiality`
  (`material`/`non_material`/`unknown` — an unrecognised changed field is
  `unknown`, never guessed); (2) flags `candidate.verification_state='stale'` when
  the source's freshest `snapshot.fetched_at` is older than its schedule window
  (stale data never counts as a fresh verification); and (3) raises a *pending*
  `review_item` (`evidence_conflict`, `recommended_action`,
  `admin_disposition='pending'`) when two **official** sources disagree on a
  *known* material fact of the same identity. There is still **no publication
  path**: reconciliation never creates or mutates `offer`/`offer_version` (its
  immutability trigger is untouched), every change event it writes is `draft`,
  and contradictions are never auto-resolved. Unknown (`None`) values never
  contradict. The migration is reversible (`alembic downgrade
  0004_ingest_candidates`).
- **Quarantine separation hardening (migration `0006_quarantine_separation`).**
  Defense-in-depth that makes "community sources can never become verified
  evidence" an enforced *database* invariant rather than only an application
  convention. It adds **no** table/column/constraint (so ORM metadata is
  unchanged and `compare_metadata` reports no drift) and installs two
  `BEFORE INSERT OR UPDATE` triggers: `trg_candidate_official_source` rejects a
  `candidate` flagged `official = true` unless its `source.trust_level =
  'official'` (a community/unverified source can never own an official candidate,
  and a quarantined candidate can never be *promoted* to official in place); and
  `trg_evidence_official_candidate` rejects an `evidence` row whose
  `candidate_id` references a non-official candidate. Together with the pre-existing
  structural isolation (`discovery_candidate` has no foreign key into `evidence`
  or `offer_version`) community-sourced discovery cannot cross into the
  verified/official pipeline at any layer. Both triggers raise with
  `ERRCODE = 'restrict_violation'` (SQLSTATE class 23 → `IntegrityError`), matching
  the offer-version immutability convention. The application half lives in
  `app.ingest.trust` (`is_official_source`, `assert_evidence_permitted`), used by
  `run_scan`. There is still **no publication path** and the offer-version
  immutability trigger is untouched. The migration is reversible (`alembic
  downgrade 0005_change_event_candidate_link`) — up→down→up restores both triggers,
  leaves the immutability trigger intact, and produces no drift.
- **Tests.** `tests/unit/test_domain_models.py` checks the metadata shape and
  vocabulary membership offline; `tests/integration/test_domain_migration.py`
  (run against a live PostgreSQL) verifies apply, model/migration drift,
  foreign-key and check-constraint enforcement, offer_version immutability,
  provenance queries, and a downgrade/re-apply round trip.
  `tests/unit/test_ingest_reconcile.py` covers the pure reconciliation logic
  (change/materiality/staleness/contradiction) and
  `tests/integration/test_ingest_reconcile.py` exercises the live end-to-end
  reconciliation path over unchanged/changed/stale/contradictory fixtures.
  `scripts/stack-smoke` asserts the domain tables and the immutability trigger
  exist on the running stack.
  `tests/unit/test_ingest_trust.py` covers the pure trust-gating rule offline and
  `tests/integration/test_ingest_separation.py` (live PostgreSQL) proves the
  quarantine invariant end-to-end: a community scan produces only
  `discovery_candidate` rows with zero evidence/offer/offer_version, the two 0006
  triggers reject any raw-SQL attempt to attach evidence to a community candidate
  or promote one to official, the official pipeline is unregressed, and migration
  0006 round-trips (up→down→up) with no drift and the immutability trigger intact.
  `scripts/stack-smoke` additionally asserts the two separation triggers exist.
- **Idempotent-sync key (migration `0007_source_slug`).** Adds a nullable
  `source.slug` column plus a `uq_source_slug` UNIQUE constraint so the
  declarative-config→database sync (`app.ingest.config_sync.sync_provider`) has a
  stable upsert key: re-running the sync matches an existing source by its slug
  (the YAML `source.id`) rather than inserting a duplicate. The column is
  additive and nullable, so pre-existing or non-config-managed rows are
  unaffected (PostgreSQL treats NULLs as distinct, so several unsynced sources may
  coexist). The ORM `Source` model gains the matching `slug` column +
  `UniqueConstraint`, so `compare_metadata` reports no drift. The migration
  installs **no** trigger and touches no other object: the offer-version
  immutability trigger and both 0006 separation triggers are left completely
  intact, and there is still **no publication path**. It is reversible (`alembic
  downgrade 0006_quarantine_separation`) — up→down→up restores the column +
  constraint, leaves all three triggers intact, and produces no drift
  (`tests/integration/test_domain_migration.py::test_source_slug_migration_0007_up_down_up`).
  `scripts/stack-smoke` additionally asserts the `source.slug` column and
  `uq_source_slug` constraint exist on the running stack.
- **Config→DB sync + scan runner (F005 slice 1).** `app.ingest.config_sync`
  turns a validated `ProviderConfig` (loaded from
  `config/examples/providers/<provider>.yaml`) into `provider` + `source` rows,
  bridging the YAML/DB field-name gaps (`source.type`→`adapter_type`,
  `source.url`→`endpoint`, `source.extraction_profile`→`parser_profile`,
  `source.schedule_ref`→`schedule`, `source.id`→`slug`, and deriving `official`
  from `trust_level`). `app.ingest.runner.run_provider_scans` (and the
  `python -m app.ingest.runner` CLI) composes `sync_provider` → per-source
  `run_scan` → `reconcile_scan` behind the offline Fetcher seam, isolating each
  source in its own `SAVEPOINT` so an un-buildable adapter is a per-source error,
  not a whole-run abort. It writes only pre-publication rows (`scan_run` /
  `snapshot` / `candidate` / official `evidence` / `discovery_candidate` / draft
  `change_event` / `review_item`); it never creates or mutates
  `offer`/`offer_version`/`quota`, and every official `evidence` row it produces
  has `offer_version_id IS NULL`. Covered by `tests/unit/test_ingest_config_sync.py`,
  `tests/unit/test_ingest_runner.py`, `tests/integration/test_ingest_config_sync.py`
  (creation + re-run idempotency, no duplicate rows), and
  `tests/integration/test_ingest_runner.py` (deterministic Cloudflare extraction,
  official evidence with NULL `offer_version_id`, zero offer/offer_version/quota).

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

### Reuse by the deterministic adviser (F006 slice 3)

The adviser (`apps/api/app/adviser/`) is a **read-only** consumer of this data
model: it adds **no table and no migration** (Alembic head stays 0007) and never
writes. It reads only the published `Offer` graph (`candidate` /
`discovery_candidate` are never queried) and re-runs `classify_offer` over each
offer's persisted facts, cross-checking the engine verdict against the stored
`zero_cost_class`; only offers where the two **agree** are usable, so the adviser
reuses the engine as the single Z0 source of truth and never re-derives Z0. Fit
decisions use exact `Decimal` end to end via the `read_api/normalize.py` Decimal
path (`normalize_amount_decimal` / `comparable_decimal`), and any unknown/
unnormalizable unit **fails closed**. `POST /adviser/recommend` runs on a read-only
session that never commits — the `offer_version` immutability trigger (SQLSTATE
`23001`) and the 0006 separation triggers are untouched.

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
