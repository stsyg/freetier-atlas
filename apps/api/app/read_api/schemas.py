"""Response schemas for the read-only catalogue API (F005 slice 3).

Pydantic response models describing the *published* catalogue exactly as the
S2 publication path persisted it. These are read-only projections: nothing here
accepts caller input beyond internal identifiers (which live on the route path,
not in a body).

Two product rules shape these models:

* **Simple labels by default (D039).** ``confidence_label`` is the primary,
  plain-language confidence field. The raw numeric score is exposed *only* inside
  the nested :class:`ConfidenceAdvanced` block returned by detail endpoints.
* **Unknown is better than guessed.** Every value that may be absent is
  ``Optional`` and is surfaced as ``null`` (or an ``"unknown"`` label) rather than
  being fabricated.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CategoryRef(BaseModel):
    """A minimal reference to a category."""

    slug: str
    name: str


class ProviderSummary(BaseModel):
    """A provider as shown in the providers list."""

    slug: str
    name: str
    type: str
    source_health: str | None = None
    completeness: float | None = None
    freshness: float | None = None
    service_count: int = 0
    published_offer_count: int = 0


class ProviderDetail(ProviderSummary):
    """A single provider with its full metadata."""

    official_domains: list[str] = []


class QuotaOut(BaseModel):
    """One quota row of the current offer version."""

    metric: str
    amount: float | None = None
    unit: str | None = None
    reset_period: str | None = None
    scope: str | None = None
    region_scope: str | None = None
    behaviour: str
    exhaustion_behaviour: str
    retention_policy: str | None = None


class ConfidenceAdvanced(BaseModel):
    """Advanced/detail-only confidence view: the raw numeric score + signals.

    Per D039 the numeric score never appears as a primary field; it lives only
    here, alongside the deterministic signals the S2 gate recorded.
    """

    score: float | None = None
    signals: dict | None = None


class OfferVersionOut(BaseModel):
    """One immutable offer version (append-only history entry)."""

    id: int
    version_number: int
    zero_cost_class: str
    confidence_label: str
    reasons: list[str] = []
    content_hash: str
    created_at: datetime | None = None


class OfferState(BaseModel):
    """An offer's current Z0 state, as shown in the category-states view."""

    offer_id: int
    offer_type: str
    zero_cost_class: str
    confidence_label: str
    status: str


class ServiceState(BaseModel):
    """A service and the current state of each of its published offers."""

    service_id: int
    canonical_name: str
    deployment_model: str
    category: CategoryRef | None = None
    offers: list[OfferState] = []


class CategoryGroup(BaseModel):
    """Services grouped under a category (``category`` is null when unassigned)."""

    category: CategoryRef | None = None
    services: list[ServiceState] = []


class CategoryStatesResponse(BaseModel):
    """The category/service states for one provider."""

    provider_slug: str
    provider_name: str
    categories: list[CategoryGroup] = []


class OfferSummary(BaseModel):
    """An offer as shown in a provider's offers list."""

    offer_id: int
    service_id: int
    service_name: str
    category: CategoryRef | None = None
    offer_type: str
    zero_cost_class: str
    status: str
    confidence_label: str
    current_version_number: int | None = None


class OfferDetail(BaseModel):
    """A published offer with its current version, Z0 reasons, and quotas."""

    offer_id: int
    provider_slug: str
    provider_name: str
    service_id: int
    service_name: str
    category: CategoryRef | None = None
    deployment_model: str
    offer_type: str
    zero_cost_class: str
    status: str
    eligibility: str | None = None
    requires_card: bool | None = None
    has_paid_dependencies: bool | None = None
    commercial_use_allowed: bool | None = None
    personal_use_allowed: bool | None = None
    first_seen_at: datetime | None = None
    last_verified_at: datetime | None = None
    current_version: OfferVersionOut | None = None
    reasons: list[str] = []
    blocking_conditions: list[str] = []
    quotas: list[QuotaOut] = []
    confidence_label: str
    completeness: float | None = None
    freshness: float | None = None
    advanced: ConfidenceAdvanced


class SourceOut(BaseModel):
    """The provenance source behind an evidence row."""

    id: int
    slug: str | None = None
    adapter_type: str
    trust_level: str
    official: bool
    endpoint: str | None = None


class SnapshotOut(BaseModel):
    """The captured snapshot behind an evidence row."""

    id: int
    content_location: str
    mime_type: str | None = None
    content_hash: str
    fetched_at: datetime | None = None


class EvidenceOut(BaseModel):
    """One official evidence row backing a published offer version."""

    id: int
    official: bool
    url: str | None = None
    title: str | None = None
    excerpt: str | None = None
    content_hash: str
    retrieved_at: datetime | None = None
    effective_at: datetime | None = None
    selector: str | None = None
    offer_version_id: int | None = None
    source: SourceOut
    snapshot: SnapshotOut


class OfferEvidenceResponse(BaseModel):
    """The official evidence + confidence for a published offer."""

    offer_id: int
    offer_version_id: int | None = None
    confidence_label: str
    advanced: ConfidenceAdvanced
    evidence: list[EvidenceOut] = []


class ChangeEventOut(BaseModel):
    """A published change event for an offer (added / modified / ...)."""

    id: int
    change_type: str
    materiality: str
    publication_status: str
    previous_version_id: int | None = None
    new_version_id: int | None = None
    occurred_at: datetime | None = None


class OfferHistoryResponse(BaseModel):
    """The append-only version history + change events for an offer."""

    offer_id: int
    versions: list[OfferVersionOut] = []
    change_events: list[ChangeEventOut] = []


class ErrorResponse(BaseModel):
    """A credential-free error payload (e.g. for a 404)."""

    detail: str


# --------------------------------------------------------------------------- #
# F006 slice 1 - search                                                       #
# --------------------------------------------------------------------------- #


class SearchResultItem(BaseModel):
    """One published offer as returned by the catalogue search endpoint.

    Carries just enough provider/service context to render a result row without a
    follow-up call; the offer detail endpoint remains the source for the full view.
    """

    offer_id: int
    provider_slug: str
    provider_name: str
    service_id: int
    service_name: str
    category: CategoryRef | None = None
    offer_type: str
    zero_cost_class: str
    status: str
    confidence_label: str
    current_version_number: int | None = None


class SearchFilters(BaseModel):
    """The filters that were actually applied (echoed back for determinism)."""

    q: str | None = None
    provider: str | None = None
    category: str | None = None
    zero_cost_class: str | None = None
    offer_type: str | None = None
    commercial_use: bool | None = None
    status: str | None = None


class SearchResponse(BaseModel):
    """A single page of catalogue search results with stable pagination meta."""

    filters: SearchFilters
    page: int
    page_size: int
    total_results: int
    total_pages: int
    results: list[SearchResultItem] = []


# --------------------------------------------------------------------------- #
# F006 slice 1 - category coverage matrix                                     #
# --------------------------------------------------------------------------- #


class ProviderCoverage(BaseModel):
    """One provider's coverage of a single canonical category.

    ``state`` is a closed, deterministic set derived strictly from published
    offers: ``verified_free`` (>=1 published Z0_TRUE_FREE offer), ``no_free_tier``
    (published offers exist but none are Z0_TRUE_FREE), or ``not_offered`` (no
    published service in that category). Nothing is guessed.
    """

    provider_slug: str
    provider_name: str
    state: str
    published_offer_count: int = 0
    free_offer_count: int = 0


class CategoryMatrixRow(BaseModel):
    """One canonical category crossed with every included provider's coverage."""

    ordinal: int
    slug: str
    name: str
    providers: list[ProviderCoverage] = []


class UncategorizedCoverage(BaseModel):
    """Published offers a provider has that are not mapped to a canonical category.

    Surfaced honestly rather than being forced into a category (the ingest
    pipeline does not yet assign categories to every service).
    """

    provider_slug: str
    provider_name: str
    published_offer_count: int = 0
    free_offer_count: int = 0


class CategoryMatrixResponse(BaseModel):
    """The 14-category taxonomy crossed with provider coverage states."""

    provider_slugs: list[str] = []
    categories: list[CategoryMatrixRow] = []
    uncategorized: list[UncategorizedCoverage] = []


# --------------------------------------------------------------------------- #
# F006 slice 1 - compare                                                      #
# --------------------------------------------------------------------------- #


class NormalizedQuotaOut(QuotaOut):
    """A quota row annotated with a conservative normalized measurement.

    The normalization fails closed: when a unit cannot be confidently normalized
    ``normalized`` is ``False``, the canonical fields are ``null``, and ``note``
    explains why -- never a guessed conversion (owner decision Q7).
    """

    normalized: bool = False
    canonical_amount: float | None = None
    canonical_unit: str | None = None
    dimension: str | None = None
    normalization_note: str | None = None


class CompareOffer(BaseModel):
    """One published offer as a normalized column in a side-by-side comparison."""

    offer_id: int
    provider_slug: str
    provider_name: str
    service_id: int
    service_name: str
    category: CategoryRef | None = None
    offer_type: str
    zero_cost_class: str
    status: str
    requires_card: bool | None = None
    has_paid_dependencies: bool | None = None
    commercial_use_allowed: bool | None = None
    personal_use_allowed: bool | None = None
    reasons: list[str] = []
    blocking_conditions: list[str] = []
    quotas: list[NormalizedQuotaOut] = []
    confidence_label: str
    completeness: float | None = None
    freshness: float | None = None
    evidence_count: int = 0
    advanced: ConfidenceAdvanced


class CompareResponse(BaseModel):
    """A normalized side-by-side comparison of a bounded set of offers."""

    offer_ids: list[int] = []
    offers: list[CompareOffer] = []
