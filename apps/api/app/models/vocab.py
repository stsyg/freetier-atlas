"""Closed vocabularies for the domain model.

These tuples mirror ``docs/DATA_MODEL.md`` exactly and are the single source of
truth for the check-constraint membership used by the ORM models and the
Alembic migration. Keeping them here (rather than inlining string lists in two
places) guarantees the models and the migration agree.
"""

from __future__ import annotations

# docs/DATA_MODEL.md -> Zero-cost classes
ZERO_COST_CLASSES: tuple[str, ...] = (
    "Z0_TRUE_FREE",
    "Z1_BILLING_EXPOSURE",
    "Z2_TEMPORARY_OR_CONDITIONAL",
    "Z3_SELF_HOSTED_BUILDING_BLOCK",
    "UNKNOWN",
)

# docs/DATA_MODEL.md -> Offer types
OFFER_TYPES: tuple[str, ...] = (
    "always_free",
    "recurring_quota",
    "new_customer_credit",
    "trial",
    "startup_program",
    "student_program",
    "open_source_program",
    "hackathon_promotion",
    "personal_use_free",
    "self_hosted_open_source",
    "other",
)

# docs/DATA_MODEL.md -> Exhaustion behaviours
EXHAUSTION_BEHAVIOURS: tuple[str, ...] = (
    "hard_stop",
    "request_rejected",
    "throttled",
    "service_sleeps",
    "read_only",
    "deployment_blocked",
    "site_disabled_until_reset",
    "resource_reclaimed",
    "data_deleted",
    "automatic_billing",
    "manual_upgrade_required",
    "unknown",
)

# docs/DATA_MODEL.md -> ChangeEvent ("Added, modified, withdrawn, or restored").
CHANGE_TYPES: tuple[str, ...] = (
    "added",
    "modified",
    "withdrawn",
    "restored",
)

# Service deployment model ("managed/self-hosted").
DEPLOYMENT_MODELS: tuple[str, ...] = (
    "managed",
    "self_hosted",
)

# Offer lifecycle status.
OFFER_STATUSES: tuple[str, ...] = (
    "active",
    "withdrawn",
    "deprecated",
    "unknown",
)

# Offer visibility.
OFFER_VISIBILITIES: tuple[str, ...] = (
    "public",
    "private",
    "unlisted",
)

# Quota hard/soft behaviour.
QUOTA_BEHAVIOURS: tuple[str, ...] = (
    "hard",
    "soft",
    "unknown",
)

# ChangeEvent materiality.
MATERIALITIES: tuple[str, ...] = (
    "material",
    "non_material",
    "unknown",
)

# ChangeEvent publication status.
PUBLICATION_STATUSES: tuple[str, ...] = (
    "draft",
    "published",
    "withheld",
)

# ScanRun status.
SCAN_STATUSES: tuple[str, ...] = (
    "running",
    "success",
    "failed",
    "partial",
)

# ReviewItem admin disposition.
REVIEW_DISPOSITIONS: tuple[str, ...] = (
    "pending",
    "approved",
    "rejected",
    "deferred",
)

# Candidate verification-state lifecycle (F004 source ingestion). This mirrors
# ``app.ingest.vocab.VERIFICATION_STATES`` (docs/ARCHITECTURE.md -> Verification
# states) exactly and is the single source of truth for the ``candidate`` table
# check constraint. A drift-guard unit test asserts the two stay identical.
VERIFICATION_STATES: tuple[str, ...] = (
    "detected",
    "extracting",
    "candidate",
    "verified",
    "verified_with_caveats",
    "conflict",
    "stale",
    "withdrawn",
    "rejected",
)

# How a community-derived discovery candidate entered the quarantine table
# (docs/SOURCE_REUSE_AND_PROVENANCE.md -> Provenance fields: "import method").
IMPORT_METHODS: tuple[str, ...] = (
    "manual",
    "community_import",
    "automated",
)

# Official-verification status of a quarantined discovery candidate. A discovery
# candidate starts ``unverified`` and can only ever become verified through the
# separate official-evidence pipeline; it is never itself published.
DISCOVERY_VERIFICATION_STATUSES: tuple[str, ...] = (
    "unverified",
    "verifying",
    "verified",
    "rejected",
)


def sql_in(values: tuple[str, ...]) -> str:
    """Render a tuple of vocabulary values as a SQL ``IN (...)`` membership list."""

    quoted = ", ".join(f"'{v}'" for v in values)
    return f"({quoted})"
