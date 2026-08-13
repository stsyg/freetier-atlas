"""Typed models for FreeTier Atlas declarative YAML configuration.

Every model forbids unknown fields (``extra="forbid"``) so that a typo in a
configuration file produces an actionable error instead of being silently
ignored. Secrets are never stored inline: fields that reference a credential
carry only the *name* of an environment variable (``*_env``) and are validated
to look like an environment-variable name, never a value.

The closed vocabularies here (zero-cost classes) mirror ``docs/DATA_MODEL.md``.
Open vocabularies that the domain has not frozen yet (source ``type``,
``trust_level``, llm ``mode``) are validated as lowercase slugs rather than hard
enumerations, so legitimate new values are accepted while malformed ones are
still rejected.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Annotated, Literal, Self, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.vocab import COVERAGE_STATES, EVIDENCE_BACKED_COVERAGE_STATES
from app.read_api.taxonomy import canonical_slugs, is_canonical_slug

# An environment-variable *name* reference (e.g. ``GEMINI_API_KEY``). Holds a
# name only; the real value is supplied by the runtime environment.
EnvVarName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")]

# A lowercase identifier slug (e.g. ``cloudflare-pages-limits``).
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$")]

# Authoritative provider x category coverage states (F008 slice S2). Kept in
# lockstep with ``app.models.vocab.COVERAGE_STATES`` -- the same closed set the
# database CHECK enforces -- by the assertion below.
CoverageState = Literal[
    "verified_free",
    "offered_no_z0",
    "not_offered",
    "incomplete",
    "stale",
    "conflicting",
    "unknown",
]

assert set(get_args(CoverageState)) == set(COVERAGE_STATES), (
    "CoverageState literal has drifted from app.models.vocab.COVERAGE_STATES"
)

#: Q9-A floor: a provider file must carry at least this many evidence-backed
#: ``verified_free`` / ``offered_no_z0`` declarations. A provider whose whole
#: coverage block is ``unknown`` is not a catalogue entry, it is a placeholder,
#: and it must be UNLOADABLE rather than quietly shipped as an empty row.
MIN_EVIDENCE_BACKED_COVERAGE = 3

# Authoritative zero-cost classes (docs/DATA_MODEL.md).
ZeroCostClass = Literal[
    "Z0_TRUE_FREE",
    "Z1_BILLING_EXPOSURE",
    "Z2_TEMPORARY_OR_CONDITIONAL",
    "Z3_SELF_HOSTED_BUILDING_BLOCK",
    "UNKNOWN",
]

_CRON_FIELD = re.compile(r"^[\d*/,\-]+$")


class _Base(BaseModel):
    """Base model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# application family (config/examples/application.example.yaml)
# --------------------------------------------------------------------------- #
class ApplicationSection(_Base):
    name: str = Field(min_length=1)
    public_url: str = Field(min_length=1)
    api_url: str = Field(min_length=1)
    environment: Literal["development", "staging", "production"]


class CatalogueSection(_Base):
    default_zero_cost_classes: list[ZeroCostClass] = Field(min_length=1)
    hide_temporary_offers_by_default: bool
    raw_snapshot_retention_days: int = Field(ge=1)


class AdminSection(_Base):
    authentication: Literal["github"]
    allowed_users: list[str] = Field(min_length=1)


class FeaturesSection(_Base):
    public_adviser: bool
    rss: bool
    discord: bool
    web_push: bool


class ApplicationConfig(_Base):
    application: ApplicationSection
    catalogue: CatalogueSection
    admin: AdminSection
    features: FeaturesSection


# --------------------------------------------------------------------------- #
# schedules family (config/examples/schedules.example.yaml)
# --------------------------------------------------------------------------- #
class CronSchedule(_Base):
    cron: str
    jitter_seconds: int = Field(default=0, ge=0)

    @field_validator("cron")
    @classmethod
    def _validate_cron(cls, value: str) -> str:
        fields = value.split()
        if len(fields) != 5:
            raise ValueError(
                "cron expression must have 5 whitespace-separated fields, "
                f"got {len(fields)}: {value!r}"
            )
        for index, field in enumerate(fields, start=1):
            if not _CRON_FIELD.match(field):
                raise ValueError(f"invalid cron field #{index} {field!r} in {value!r}")
        return value


class ConflictRecheck(_Base):
    delay_minutes: int = Field(ge=1)
    maximum_attempts: int = Field(ge=1)


class ScheduleSet(_Base):
    rss: CronSchedule
    structured_apis: CronSchedule
    mcp_documentation: CronSchedule
    official_pages: CronSchedule
    full_reconciliation: CronSchedule
    conflict_recheck: ConflictRecheck


class SchedulesConfig(_Base):
    schedules: ScheduleSet


# --------------------------------------------------------------------------- #
# llm-providers family (config/examples/llm-providers.example.yaml)
# --------------------------------------------------------------------------- #
class PublicAdviserLimits(_Base):
    ai_requests_per_ip_per_day: int = Field(ge=0)
    deterministic_requests_per_ip_per_day: int = Field(ge=0)
    concurrent_requests_per_session: int = Field(ge=1)
    maximum_input_characters: int = Field(ge=1)
    maximum_output_tokens: int = Field(ge=1)
    require_captcha: bool
    reject_urls: bool
    allow_file_uploads: bool
    fallback_to_deterministic: bool


class LlmProvider(_Base):
    enabled: bool
    base_url_env: EnvVarName | None = None
    api_key_env: EnvVarName | None = None
    model: str | None = None
    external_processing_consent_required: bool | None = None


class LlmSection(_Base):
    mode: Slug
    public_adviser: PublicAdviserLimits
    providers: dict[str, LlmProvider] = Field(min_length=1)


class LlmProvidersConfig(_Base):
    llm: LlmSection


# --------------------------------------------------------------------------- #
# provider family (config/examples/providers/*.yaml)
# --------------------------------------------------------------------------- #
class ProviderSection(_Base):
    id: Slug
    name: str = Field(min_length=1)
    official_domains: list[str] = Field(min_length=1)


class Source(_Base):
    id: Slug
    type: Slug
    trust_level: Slug
    schedule_ref: Slug
    url: str | None = None
    extraction_profile: Slug | None = None
    capabilities: list[str] | None = None

    @model_validator(mode="after")
    def _check_type_requirements(self) -> Self:
        if self.type == "mcp":
            if not self.capabilities:
                raise ValueError(f"source {self.id!r}: mcp source requires 'capabilities'")
        elif not self.url:
            raise ValueError(f"source {self.id!r}: {self.type} source requires 'url'")
        return self


class PublishingSection(_Base):
    automatic_threshold: float = Field(ge=0.0, le=1.0)
    uncertain_threshold: float = Field(ge=0.0, le=1.0)
    require_official_source: bool
    require_deterministic_numeric_validation: bool

    @model_validator(mode="after")
    def _check_thresholds(self) -> Self:
        if self.automatic_threshold < self.uncertain_threshold:
            raise ValueError(
                "automatic_threshold must be greater than or equal to uncertain_threshold "
                f"(got automatic={self.automatic_threshold}, uncertain={self.uncertain_threshold})"
            )
        return self


class CoverageDeclaration(_Base):
    """One provider's declared state for one canonical category (F008 slice S2).

    Every provider file must declare all fourteen canonical categories, so the
    catalogue never has to infer coverage from missing data. The honesty rules
    below mirror the database CHECK constraints on
    ``provider_category_coverage`` exactly, so a bad declaration fails at config
    load rather than at ``INSERT``.
    """

    state: CoverageState
    #: Required when ``state`` is ``not_offered``: asserting that a provider does
    #: not offer a category is a claim, so it must say why.
    rationale: str | None = None
    #: Id of a source declared in this same file.
    source: Slug | None = None
    evidence_url: str | None = None

    @property
    def has_provenance(self) -> bool:
        return bool(self.source) or bool((self.evidence_url or "").strip())


class ProviderConfig(_Base):
    provider: ProviderSection
    # Explicitly empty is valid for coverage-only providers whose current
    # official evidence cannot yet be represented by an ingest adapter. The
    # field remains required so an accidental omission is still actionable.
    sources: list[Source]
    publishing: PublishingSection

    #: Explicit provider x category coverage (F008 slice S2). **Mandatory**, and
    #: must contain exactly the fourteen canonical slugs: an omission is an
    #: error rather than an implicit ``unknown``, because the whole point of the
    #: slice is that every pair carries a deliberate, reviewable declaration.
    coverage: dict[str, CoverageDeclaration]

    #: Declared service -> canonical category mapping (F008 slice S1).
    #:
    #: The key is a service's ``canonical_name`` exactly as it appears in the
    #: extracted candidate facts' ``service`` field (e.g. ``"Cloudflare
    #: Workers"``); the value is one of the fourteen canonical category slugs in
    #: ``app.read_api.taxonomy.CATEGORY_TAXONOMY``.
    #:
    #: Category is *declared structural metadata*, never an offer fact and never
    #: inferred: a service that is absent from this map stays uncategorised
    #: (``service.category_id IS NULL``) and is reported honestly in the
    #: ``uncategorized`` rollup rather than guessed into a category. Declaring a
    #: service name that does not exist is deliberately **not** an error -- the
    #: mapping may legitimately be declared before the service is first
    #: discovered -- it is simply a no-op at sync time.
    service_categories: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_service_categories(self) -> Self:
        for service_name, slug in self.service_categories.items():
            if not service_name.strip():
                raise ValueError(
                    f"provider {self.provider.id!r}: service_categories contains a blank "
                    "service name; the key must be the service's canonical_name as it "
                    "appears in the extracted candidate facts"
                )
            if not is_canonical_slug(slug):
                raise ValueError(
                    f"provider {self.provider.id!r}: service_categories[{service_name!r}] = "
                    f"{slug!r} is not one of the fourteen canonical category slugs. "
                    f"Valid slugs (apps/api/app/read_api/taxonomy.py): "
                    f"{', '.join(canonical_slugs())}"
                )
        return self

    @model_validator(mode="after")
    def _check_unique_source_ids(self) -> Self:
        """Reject ambiguous source references before building the lookup set."""

        counts = Counter(source.id for source in self.sources)
        duplicates = sorted(source_id for source_id, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(
                f"provider {self.provider.id!r}: sources contains duplicate source ids: "
                f"{', '.join(duplicates)}. Every sources[].id must be unique; remove or "
                "rename the duplicate entries."
            )
        return self

    @model_validator(mode="after")
    def _check_coverage(self) -> Self:
        """Exactly the fourteen canonical slugs, each obeying the honesty rules."""

        expected = set(canonical_slugs())
        declared = set(self.coverage)

        missing = sorted(expected - declared)
        if missing:
            raise ValueError(
                f"provider {self.provider.id!r}: coverage is missing "
                f"{len(missing)} of the fourteen canonical categories: "
                f"{', '.join(missing)}. Every provider x category pair needs an "
                "explicit state (use 'unknown' where nothing has been verified) "
                "so the catalogue never has to guess."
            )
        unknown_slugs = sorted(declared - expected)
        if unknown_slugs:
            raise ValueError(
                f"provider {self.provider.id!r}: coverage declares "
                f"{len(unknown_slugs)} slug(s) that are not part of the canonical "
                f"taxonomy: {', '.join(unknown_slugs)}. Valid slugs "
                f"(apps/api/app/read_api/taxonomy.py): {', '.join(canonical_slugs())}"
            )

        source_ids = {source.id for source in self.sources}
        for slug in sorted(declared):
            entry = self.coverage[slug]
            if entry.state == "not_offered" and not (entry.rationale or "").strip():
                raise ValueError(
                    f"provider {self.provider.id!r}: coverage[{slug!r}] declares "
                    "'not_offered' without a 'rationale'. Asserting that a provider "
                    "does not offer a category is a claim and must state why; use "
                    "'unknown' if it simply has not been checked."
                )
            if entry.state in EVIDENCE_BACKED_COVERAGE_STATES and not entry.has_provenance:
                raise ValueError(
                    f"provider {self.provider.id!r}: coverage[{slug!r}] declares "
                    f"{entry.state!r} without provenance. A claim that an offer "
                    "exists requires a 'source' (declared in this file) or an "
                    "'evidence_url'."
                )
            if entry.source is not None and entry.source not in source_ids:
                raise ValueError(
                    f"provider {self.provider.id!r}: coverage[{slug!r}] references "
                    f"source {entry.source!r}, which is not declared in this file. "
                    f"Declared sources: {', '.join(sorted(source_ids)) or 'none'}"
                )
        return self

    @model_validator(mode="after")
    def validate_coverage_floor(self) -> Self:
        """Q9-A: reject a provider file with too little evidence-backed coverage.

        At least :data:`MIN_EVIDENCE_BACKED_COVERAGE` entries must declare
        ``verified_free`` or ``offered_no_z0`` **and** carry a source or an
        evidence URL. This makes an all-``unknown`` provider file a hard load
        failure rather than a warning: a provider nobody has verified anything
        about must not be able to appear in the catalogue at all.
        """

        backed = sorted(
            slug
            for slug, entry in self.coverage.items()
            if entry.state in EVIDENCE_BACKED_COVERAGE_STATES and entry.has_provenance
        )
        if len(backed) < MIN_EVIDENCE_BACKED_COVERAGE:
            shortfall = MIN_EVIDENCE_BACKED_COVERAGE - len(backed)
            found = ", ".join(backed) if backed else "none"
            raise ValueError(
                f"provider {self.provider.id!r}: coverage declares only "
                f"{len(backed)} evidence-backed "
                f"{'/'.join(EVIDENCE_BACKED_COVERAGE_STATES)} categories "
                f"(found: {found}); at least {MIN_EVIDENCE_BACKED_COVERAGE} are "
                f"required -- {shortfall} more needed. A provider with no verified "
                "coverage is a placeholder, not a catalogue entry."
            )
        return self


# Registry of configuration families -> root model.
FAMILY_MODELS: dict[str, type[_Base]] = {
    "application": ApplicationConfig,
    "schedules": SchedulesConfig,
    "llm-providers": LlmProvidersConfig,
    "provider": ProviderConfig,
}
