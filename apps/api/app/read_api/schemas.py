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


class EvidenceCurrencyOut(BaseModel):
    """Whether the official evidence behind a claim is still inside its window.

    Recomputed on every read against the request's clock and never stored
    (decision Q11). It exists because every other confidence-shaped field on
    these models is **frozen at publish time** and cannot know that the evidence
    underneath it later expired -- which is how a five-year-expired claim came to
    report a freshness of ``1.0``.

    Three fields, not one flag, because there are two distinct ways a claim can
    fail to be current and collapsing them is how a gap hides:

    * ``current=True``                   -- checked, and inside its window.
    * ``stale=True``  (``checked=True``) -- we looked, and it has expired.
    * ``checked=False``                  -- we could not look at all. **This must
      never read as fresh.** There is no fetch time to compare against, so no
      statement about currency is available.

    ``freshness`` is ``None`` -- never ``0.0`` -- when ``checked`` is false. The
    distinction survives all the way to the rendered page: the web formatter
    shows ``null`` as "Unknown" but ``0`` as "0%", and a "0%" where there is no
    measurement would reproduce the original defect one layer up.
    """

    #: The single predicate a client should gate a *free* claim on.
    current: bool = False
    #: Was a currency check possible at all (did a fetch time exist)?
    checked: bool = False
    #: True only when we checked AND the evidence is past its window.
    stale: bool = False
    #: Read-time freshness in [0, 1]; ``None`` when no check was possible.
    freshness: float | None = None
    #: Age of the oldest backing evidence, in days.
    age_days: float | None = None
    #: The refresh window that age was compared against, in days.
    window_days: float | None = None
    #: When the oldest backing evidence was fetched.
    oldest_fetched_at: datetime | None = None
    #: One-line plain-language explanation; ``None`` when current.
    reason: str | None = None


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
    #: The provider's LEAST current published claim. A provider is only as
    #: current as its stalest support, so this is a rollup and not an average:
    #: averaging would let one fresh offer mask an expired one.
    evidence_currency: EvidenceCurrencyOut = EvidenceCurrencyOut()


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
    #: Read-time currency of the evidence behind THIS version's claim. A history
    #: view repeats a class per version, so each row carries its own verdict
    #: rather than inheriting the current one.
    evidence_currency: EvidenceCurrencyOut = EvidenceCurrencyOut()


class OfferState(BaseModel):
    """An offer's current Z0 state, as shown in the category-states view."""

    offer_id: int
    offer_type: str
    zero_cost_class: str
    confidence_label: str
    status: str
    evidence_currency: EvidenceCurrencyOut = EvidenceCurrencyOut()


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
    evidence_currency: EvidenceCurrencyOut = EvidenceCurrencyOut()


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
    evidence_currency: EvidenceCurrencyOut = EvidenceCurrencyOut()


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
    evidence_currency: EvidenceCurrencyOut = EvidenceCurrencyOut()
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
    evidence_currency: EvidenceCurrencyOut = EvidenceCurrencyOut()


class SearchFilters(BaseModel):
    """The filters that were actually applied (echoed back for determinism)."""

    q: str | None = None
    provider: str | None = None
    category: str | None = None
    zero_cost_class: str | None = None
    offer_type: str | None = None
    commercial_use: bool | None = None
    status: str | None = None
    #: Evidence currency, a filter dimension DISTINCT from ``zero_cost_class``.
    #: The class is a classification of the offer's terms; currency is a property
    #: of the evidence behind it. Conflating them is what made a "show me free
    #: things" filter imply a freshness it never checked. ``None`` means "any",
    #: and is the default: an expired claim is returned LABELLED, not hidden,
    #: because a wrongly-omitted free offer is its own defect.
    evidence_current: bool | None = None


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
    """One provider's coverage of a single canonical category (F008 slice S2).

    Three fields carry the state, and they mean different things:

    * ``declared_state`` -- what a human declared in the provider config, with a
      rationale and/or provenance. ``None`` when the pair has no declaration.
    * ``derived_state`` -- what the *published* catalogue supports right now,
      recomputed on every request and never stored (decision Q11). It is never
      ``not_offered``: an empty catalogue means "not verified", not "not
      offered".
    * ``state`` -- what to display: ``unknown`` when undeclared, ``conflicting``
      when the declaration and the derivation materially disagree, ``stale``
      when a declared ``verified_free`` rests on evidence past its refresh
      window, otherwise the declaration. The other two fields stay visible so
      nothing is hidden.

    ``mismatch`` and ``state`` are deliberately independent. A declared
    ``verified_free`` in a category the catalogue has never published an offer
    for sets ``mismatch`` (a human should reconcile it) while still displaying
    the provenance-backed declaration, because an absent publication refutes
    nothing.

    A zero published-offer count therefore never produces ``not_offered``;
    ``not_offered`` only ever arrives as an explicit, reasoned declaration.
    """

    provider_slug: str
    provider_name: str
    state: str
    declared_state: str | None = None
    derived_state: str = "unknown"
    mismatch: bool = False
    rationale: str | None = None
    evidence_url: str | None = None
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
    evidence_currency: EvidenceCurrencyOut = EvidenceCurrencyOut()


class CompareResponse(BaseModel):
    """A normalized side-by-side comparison of a bounded set of offers."""

    offer_ids: list[int] = []
    offers: list[CompareOffer] = []
