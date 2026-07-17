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
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# An environment-variable *name* reference (e.g. ``GEMINI_API_KEY``). Holds a
# name only; the real value is supplied by the runtime environment.
EnvVarName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")]

# A lowercase identifier slug (e.g. ``cloudflare-pages-limits``).
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$")]

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


class ProviderConfig(_Base):
    provider: ProviderSection
    sources: list[Source] = Field(min_length=1)
    publishing: PublishingSection


# Registry of configuration families -> root model.
FAMILY_MODELS: dict[str, type[_Base]] = {
    "application": ApplicationConfig,
    "schedules": SchedulesConfig,
    "llm-providers": LlmProvidersConfig,
    "provider": ProviderConfig,
}
