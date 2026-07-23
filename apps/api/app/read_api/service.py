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
from .schemas import (
    CategoryGroup,
    CategoryRef,
    CategoryStatesResponse,
    ChangeEventOut,
    ConfidenceAdvanced,
    EvidenceOut,
    OfferDetail,
    OfferEvidenceResponse,
    OfferHistoryResponse,
    OfferState,
    OfferSummary,
    OfferVersionOut,
    ProviderDetail,
    ProviderSummary,
    QuotaOut,
    ServiceState,
    SnapshotOut,
    SourceOut,
)


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
