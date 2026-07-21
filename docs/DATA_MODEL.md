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
