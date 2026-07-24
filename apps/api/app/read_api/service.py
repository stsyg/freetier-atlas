"""ORM -> schema serialization for the read-only catalogue API (F005 slice 3).

This module turns the persisted, *published* ORM graph into the response
schemas. It is where the S2 ``material_facts`` JSONB is read back:

* the Z0 class + human-readable reasons come from ``material_facts.classification``,
* the confidence LABEL (primary) is derived from ``material_facts.confidence``
  using the version's own persisted ``gate`` thresholds, and the raw numeric
  score + signals are only ever placed in the advanced/detail block,
* per-offer completeness / freshness come from ``confidence_signals``, and
  per-provider values fall back to averaging the published offers' signals when
  the provider columns are unset.

Nothing here fabricates a value: an unknown field is serialized as ``null`` (or
the ``"unknown"`` confidence label).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import ceil
from statistics import fmean

from app.models.domain import (
    Category,
    ChangeEvent,
    Evidence,
    Offer,
    OfferVersion,
    Provider,
)

from . import queries
from .confidence import confidence_label
from .normalize import normalize_amount
from .schemas import (
    CategoryGroup,
    CategoryMatrixResponse,
    CategoryMatrixRow,
    CategoryRef,
    CategoryStatesResponse,
    ChangeEventOut,
    CompareOffer,
    CompareResponse,
    ConfidenceAdvanced,
    EvidenceOut,
    NormalizedQuotaOut,
    OfferDetail,
    OfferEvidenceResponse,
    OfferHistoryResponse,
    OfferState,
    OfferSummary,
    OfferVersionOut,
    ProviderCoverage,
    ProviderDetail,
    ProviderSummary,
    QuotaOut,
    SearchFilters,
    SearchResponse,
    SearchResultItem,
    ServiceState,
    SnapshotOut,
    SourceOut,
    UncategorizedCoverage,
)
from .search import SearchPage, SearchParams
from .taxonomy import CATEGORY_TAXONOMY, is_canonical_slug


def _as_float(value: object) -> float | None:
    """Coerce a value (Decimal / int / str) to ``float`` or return ``None``."""

    if value is None:
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def _facts(version: OfferVersion | None) -> Mapping[str, object]:
    if version is None or not isinstance(version.material_facts, Mapping):
        return {}
    return version.material_facts


def _label_for(version: OfferVersion | None) -> str:
    facts = _facts(version)
    gate = facts.get("gate") if isinstance(facts.get("gate"), Mapping) else {}
    return confidence_label(
        _as_float(facts.get("confidence")),
        automatic_threshold=_as_float(gate.get("automatic_threshold")),
        uncertain_threshold=_as_float(gate.get("uncertain_threshold")),
    )


def _classification(version: OfferVersion | None) -> Mapping[str, object]:
    facts = _facts(version)
    block = facts.get("classification")
    return block if isinstance(block, Mapping) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


def _signal(version: OfferVersion | None, key: str) -> float | None:
    facts = _facts(version)
    signals = facts.get("confidence_signals")
    if not isinstance(signals, Mapping):
        return None
    return _as_float(signals.get(key))


def _category_ref(
    service_category_id: int | None, cat_map: Mapping[int, Category]
) -> CategoryRef | None:
    if service_category_id is None:
        return None
    category = cat_map.get(service_category_id)
    if category is None:
        return None
    return CategoryRef(slug=category.slug, name=category.name)


def serialize_version(version: OfferVersion) -> OfferVersionOut:
    """Serialize one immutable offer version (history / current view)."""

    classification = _classification(version)
    return OfferVersionOut(
        id=version.id,
        version_number=version.version_number,
        zero_cost_class=version.zero_cost_class,
        confidence_label=_label_for(version),
        reasons=_string_list(classification.get("reasons")),
        content_hash=version.content_hash,
        created_at=version.created_at,
    )


def serialize_quota(quota: object) -> QuotaOut:
    return QuotaOut(
        metric=quota.metric,
        amount=_as_float(quota.amount),
        unit=quota.unit,
        reset_period=quota.reset_period,
        scope=quota.scope,
        region_scope=quota.region_scope,
        behaviour=quota.behaviour,
        exhaustion_behaviour=quota.exhaustion_behaviour,
        retention_policy=quota.retention_policy,
    )


def _provider_scores(provider: Provider) -> tuple[float | None, float | None]:
    """Provider completeness / freshness: stored columns, else averaged signals."""

    completeness = _as_float(provider.completeness_score)
    freshness = _as_float(provider.freshness_score)

    if completeness is not None and freshness is not None:
        return completeness, freshness

    comp_values: list[float] = []
    fresh_values: list[float] = []
    for service in provider.services:
        for offer in service.offers:
            version = queries.latest_version(offer)
            if version is None:
                continue
            comp = _signal(version, "completeness")
            fresh = _signal(version, "freshness")
            if comp is not None:
                comp_values.append(comp)
            if fresh is not None:
                fresh_values.append(fresh)

    if completeness is None and comp_values:
        completeness = round(fmean(comp_values), 4)
    if freshness is None and fresh_values:
        freshness = round(fmean(fresh_values), 4)
    return completeness, freshness


def _counts(provider: Provider) -> tuple[int, int]:
    service_count = len(provider.services)
    published = sum(1 for s in provider.services for o in s.offers if queries.is_published(o))
    return service_count, published


def serialize_provider_summary(provider: Provider) -> ProviderSummary:
    completeness, freshness = _provider_scores(provider)
    service_count, published = _counts(provider)
    return ProviderSummary(
        slug=provider.slug,
        name=provider.name,
        type=provider.type,
        source_health=provider.source_health,
        completeness=completeness,
        freshness=freshness,
        service_count=service_count,
        published_offer_count=published,
    )


def serialize_provider_detail(provider: Provider) -> ProviderDetail:
    completeness, freshness = _provider_scores(provider)
    service_count, published = _counts(provider)
    domains = provider.official_domains if isinstance(provider.official_domains, list) else []
    return ProviderDetail(
        slug=provider.slug,
        name=provider.name,
        type=provider.type,
        source_health=provider.source_health,
        completeness=completeness,
        freshness=freshness,
        service_count=service_count,
        published_offer_count=published,
        official_domains=[str(d) for d in domains],
    )


def serialize_category_states(
    provider: Provider, cat_map: Mapping[int, Category]
) -> CategoryStatesResponse:
    """Group the provider's published offers by category -> service -> offer state."""

    groups: dict[int | None, CategoryGroup] = {}
    for service in provider.services:
        published_offers = [o for o in service.offers if queries.is_published(o)]
        if not published_offers:
            continue
        state = ServiceState(
            service_id=service.id,
            canonical_name=service.canonical_name,
            deployment_model=service.deployment_model,
            category=_category_ref(service.category_id, cat_map),
            offers=[
                OfferState(
                    offer_id=offer.id,
                    offer_type=offer.offer_type,
                    zero_cost_class=offer.zero_cost_class,
                    confidence_label=_label_for(queries.latest_version(offer)),
                    status=offer.status,
                )
                for offer in published_offers
            ],
        )
        key = service.category_id
        if key not in groups:
            groups[key] = CategoryGroup(
                category=_category_ref(service.category_id, cat_map), services=[]
            )
        groups[key].services.append(state)

    # Deterministic order: categorized groups by slug, uncategorized last.
    ordered = sorted(
        groups.values(),
        key=lambda g: (g.category is None, g.category.slug if g.category else ""),
    )
    return CategoryStatesResponse(
        provider_slug=provider.slug,
        provider_name=provider.name,
        categories=ordered,
    )


def serialize_offer_summaries(
    provider: Provider, cat_map: Mapping[int, Category]
) -> list[OfferSummary]:
    summaries: list[OfferSummary] = []
    for service in provider.services:
        for offer in service.offers:
            if not queries.is_published(offer):
                continue
            version = queries.latest_version(offer)
            summaries.append(
                OfferSummary(
                    offer_id=offer.id,
                    service_id=service.id,
                    service_name=service.canonical_name,
                    category=_category_ref(service.category_id, cat_map),
                    offer_type=offer.offer_type,
                    zero_cost_class=offer.zero_cost_class,
                    status=offer.status,
                    confidence_label=_label_for(version),
                    current_version_number=version.version_number if version else None,
                )
            )
    summaries.sort(key=lambda s: s.offer_id)
    return summaries


def serialize_offer_detail(offer: Offer, cat_map: Mapping[int, Category]) -> OfferDetail:
    version = queries.latest_version(offer)
    service = offer.service
    provider = service.provider
    classification = _classification(version)
    facts = _facts(version)

    quotas = [serialize_quota(q) for q in (version.quotas if version else [])]

    return OfferDetail(
        offer_id=offer.id,
        provider_slug=provider.slug,
        provider_name=provider.name,
        service_id=service.id,
        service_name=service.canonical_name,
        category=_category_ref(service.category_id, cat_map),
        deployment_model=service.deployment_model,
        offer_type=offer.offer_type,
        zero_cost_class=offer.zero_cost_class,
        status=offer.status,
        eligibility=offer.eligibility,
        requires_card=offer.requires_card,
        has_paid_dependencies=offer.has_paid_dependencies,
        commercial_use_allowed=offer.commercial_use_allowed,
        personal_use_allowed=offer.personal_use_allowed,
        first_seen_at=offer.first_seen_at,
        last_verified_at=offer.last_verified_at,
        current_version=serialize_version(version) if version else None,
        reasons=_string_list(classification.get("reasons")),
        blocking_conditions=_string_list(classification.get("blocking_conditions")),
        quotas=quotas,
        confidence_label=_label_for(version),
        completeness=_signal(version, "completeness"),
        freshness=_signal(version, "freshness"),
        advanced=ConfidenceAdvanced(
            score=_as_float(facts.get("confidence")),
            signals=dict(facts.get("confidence_signals"))
            if isinstance(facts.get("confidence_signals"), Mapping)
            else None,
        ),
    )


def serialize_source(source: object) -> SourceOut:
    return SourceOut(
        id=source.id,
        slug=source.slug,
        adapter_type=source.adapter_type,
        trust_level=source.trust_level,
        official=source.official,
        endpoint=source.endpoint,
    )


def serialize_snapshot(snapshot: object) -> SnapshotOut:
    return SnapshotOut(
        id=snapshot.id,
        content_location=snapshot.content_location,
        mime_type=snapshot.mime_type,
        content_hash=snapshot.content_hash,
        fetched_at=snapshot.fetched_at,
    )


def serialize_evidence_row(evidence: Evidence) -> EvidenceOut:
    return EvidenceOut(
        id=evidence.id,
        official=evidence.official,
        url=evidence.url,
        title=evidence.title,
        excerpt=evidence.excerpt,
        content_hash=evidence.content_hash,
        retrieved_at=evidence.retrieved_at,
        effective_at=evidence.effective_at,
        selector=evidence.selector,
        offer_version_id=evidence.offer_version_id,
        source=serialize_source(evidence.source),
        snapshot=serialize_snapshot(evidence.snapshot),
    )


def serialize_offer_evidence(
    offer: Offer, evidence_rows: Sequence[Evidence]
) -> OfferEvidenceResponse:
    version = queries.latest_version(offer)
    facts = _facts(version)
    return OfferEvidenceResponse(
        offer_id=offer.id,
        offer_version_id=version.id if version else None,
        confidence_label=_label_for(version),
        advanced=ConfidenceAdvanced(
            score=_as_float(facts.get("confidence")),
            signals=dict(facts.get("confidence_signals"))
            if isinstance(facts.get("confidence_signals"), Mapping)
            else None,
        ),
        evidence=[serialize_evidence_row(e) for e in evidence_rows],
    )


def serialize_change_event(event: ChangeEvent) -> ChangeEventOut:
    return ChangeEventOut(
        id=event.id,
        change_type=event.change_type,
        materiality=event.materiality,
        publication_status=event.publication_status,
        previous_version_id=event.previous_version_id,
        new_version_id=event.new_version_id,
        occurred_at=event.occurred_at,
    )


def serialize_offer_history(
    offer_id: int,
    versions: Sequence[OfferVersion],
    change_events: Sequence[ChangeEvent],
) -> OfferHistoryResponse:
    return OfferHistoryResponse(
        offer_id=offer_id,
        versions=[serialize_version(v) for v in versions],
        change_events=[serialize_change_event(e) for e in change_events],
    )


# --------------------------------------------------------------------------- #
# F006 slice 1 - search                                                       #
# --------------------------------------------------------------------------- #

#: The zero-cost class that proves a genuinely-free offer (drives the coverage
#: matrix ``verified_free`` state).
_FREE_CLASS = "Z0_TRUE_FREE"


def _search_result_item(offer: Offer, cat_map: Mapping[int, Category]) -> SearchResultItem:
    service = offer.service
    provider = service.provider
    version = queries.latest_version(offer)
    return SearchResultItem(
        offer_id=offer.id,
        provider_slug=provider.slug,
        provider_name=provider.name,
        service_id=service.id,
        service_name=service.canonical_name,
        category=_category_ref(service.category_id, cat_map),
        offer_type=offer.offer_type,
        zero_cost_class=offer.zero_cost_class,
        status=offer.status,
        confidence_label=_label_for(version),
        current_version_number=version.version_number if version else None,
    )


def serialize_search_response(
    page: SearchPage, params: SearchParams, cat_map: Mapping[int, Category]
) -> SearchResponse:
    """Serialize an executed :class:`SearchPage` into the search response schema."""

    total_pages = ceil(page.total / page.page_size) if page.page_size else 0
    return SearchResponse(
        filters=SearchFilters(
            q=params.q,
            provider=params.provider,
            category=params.category,
            zero_cost_class=params.zero_cost_class,
            offer_type=params.offer_type,
            commercial_use=params.commercial_use,
            status=params.status,
        ),
        page=page.page,
        page_size=page.page_size,
        total_results=page.total,
        total_pages=total_pages,
        results=[_search_result_item(o, cat_map) for o in page.offers],
    )


# --------------------------------------------------------------------------- #
# F006 slice 1 - category coverage matrix                                     #
# --------------------------------------------------------------------------- #


def _coverage_state(published: int, free: int) -> str:
    if published == 0:
        return "not_offered"
    if free > 0:
        return "verified_free"
    return "no_free_tier"


def serialize_category_matrix(
    providers: Sequence[Provider], cat_map: Mapping[int, Category]
) -> CategoryMatrixResponse:
    """Cross the canonical 14-category taxonomy with each provider's coverage.

    Coverage is derived strictly from *published* offers. A published service
    whose category is absent or is not one of the fourteen canonical slugs is not
    guessed into a category -- it is rolled up honestly into a per-provider
    ``uncategorized`` tally.
    """

    ordered_providers = sorted(providers, key=lambda p: p.slug)

    # (provider_slug, canonical_slug|None) -> [published_count, free_count]
    tally: dict[tuple[str, str | None], list[int]] = {}
    for provider in ordered_providers:
        for service in provider.services:
            category = cat_map.get(service.category_id) if service.category_id else None
            slug = category.slug if category and is_canonical_slug(category.slug) else None
            for offer in service.offers:
                if not queries.is_published(offer):
                    continue
                bucket = tally.setdefault((provider.slug, slug), [0, 0])
                bucket[0] += 1
                if offer.zero_cost_class == _FREE_CLASS:
                    bucket[1] += 1

    rows: list[CategoryMatrixRow] = []
    for taxon in CATEGORY_TAXONOMY:
        coverages: list[ProviderCoverage] = []
        for provider in ordered_providers:
            published, free = tally.get((provider.slug, taxon.slug), [0, 0])
            coverages.append(
                ProviderCoverage(
                    provider_slug=provider.slug,
                    provider_name=provider.name,
                    state=_coverage_state(published, free),
                    published_offer_count=published,
                    free_offer_count=free,
                )
            )
        rows.append(
            CategoryMatrixRow(
                ordinal=taxon.ordinal, slug=taxon.slug, name=taxon.name, providers=coverages
            )
        )

    uncategorized: list[UncategorizedCoverage] = []
    for provider in ordered_providers:
        published, free = tally.get((provider.slug, None), [0, 0])
        if published == 0:
            continue
        uncategorized.append(
            UncategorizedCoverage(
                provider_slug=provider.slug,
                provider_name=provider.name,
                published_offer_count=published,
                free_offer_count=free,
            )
        )

    return CategoryMatrixResponse(
        provider_slugs=[p.slug for p in ordered_providers],
        categories=rows,
        uncategorized=uncategorized,
    )


# --------------------------------------------------------------------------- #
# F006 slice 1 - compare                                                      #
# --------------------------------------------------------------------------- #


def _normalized_quota(quota: object) -> NormalizedQuotaOut:
    base = serialize_quota(quota)
    result = normalize_amount(quota.amount, quota.unit)
    return NormalizedQuotaOut(
        **base.model_dump(),
        normalized=result.normalized,
        canonical_amount=result.canonical_amount,
        canonical_unit=result.canonical_unit,
        dimension=result.dimension,
        normalization_note=result.note,
    )


def _compare_offer(offer: Offer, cat_map: Mapping[int, Category]) -> CompareOffer:
    service = offer.service
    provider = service.provider
    version = queries.latest_version(offer)
    classification = _classification(version)
    facts = _facts(version)
    quotas = version.quotas if version else []
    evidence_count = len(version.evidence) if version else 0

    return CompareOffer(
        offer_id=offer.id,
        provider_slug=provider.slug,
        provider_name=provider.name,
        service_id=service.id,
        service_name=service.canonical_name,
        category=_category_ref(service.category_id, cat_map),
        offer_type=offer.offer_type,
        zero_cost_class=offer.zero_cost_class,
        status=offer.status,
        requires_card=offer.requires_card,
        has_paid_dependencies=offer.has_paid_dependencies,
        commercial_use_allowed=offer.commercial_use_allowed,
        personal_use_allowed=offer.personal_use_allowed,
        reasons=_string_list(classification.get("reasons")),
        blocking_conditions=_string_list(classification.get("blocking_conditions")),
        quotas=[_normalized_quota(q) for q in quotas],
        confidence_label=_label_for(version),
        completeness=_signal(version, "completeness"),
        freshness=_signal(version, "freshness"),
        evidence_count=evidence_count,
        advanced=ConfidenceAdvanced(
            score=_as_float(facts.get("confidence")),
            signals=dict(facts.get("confidence_signals"))
            if isinstance(facts.get("confidence_signals"), Mapping)
            else None,
        ),
    )


def serialize_compare(
    offer_ids: Sequence[int], offers: Sequence[Offer], cat_map: Mapping[int, Category]
) -> CompareResponse:
    """Serialize a bounded set of published offers into a side-by-side comparison.

    ``offers`` is expected to already be resolved + published (the router rejects
    unknown/unpublished ids with 404) and presented in the caller's requested
    order; ``offer_ids`` echoes that requested order for the client.
    """

    return CompareResponse(
        offer_ids=list(offer_ids),
        offers=[_compare_offer(o, cat_map) for o in offers],
    )
