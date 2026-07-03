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
