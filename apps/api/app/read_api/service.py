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

from . import coverage, queries
from .confidence import confidence_label
from .currency import (
    NO_CURRENCY,
    CurrencyContext,
    EvidenceCurrency,
    confidence_label_for,
    freshness_or_none,
    is_publishable_free_claim,
)
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
    EvidenceCurrencyOut,
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


def _label_for(version: OfferVersion | None, currency: EvidenceCurrency) -> str:
    """The confidence label, capped by what the evidence STILL supports.

    ``material_facts.confidence`` was frozen at publish time and cannot know its
    evidence later expired, so the persisted label is passed through
    :func:`confidence_label_for`. That only ever removes unearned confidence --
    a current claim is returned untouched.
    """

    facts = _facts(version)
    gate = facts.get("gate") if isinstance(facts.get("gate"), Mapping) else {}
    persisted = confidence_label(
        _as_float(facts.get("confidence")),
        automatic_threshold=_as_float(gate.get("automatic_threshold")),
        uncertain_threshold=_as_float(gate.get("uncertain_threshold")),
    )
    return confidence_label_for(persisted, currency)


def _days(delta: object) -> float | None:
    """Render a ``timedelta`` as fractional days, or ``None`` when absent."""

    if delta is None:
        return None
    return round(delta.total_seconds() / 86400.0, 6)  # type: ignore[union-attr]


def _currency_out(currency: EvidenceCurrency) -> EvidenceCurrencyOut:
    """Project a read-time currency verdict onto the wire schema.

    ``freshness`` goes through :func:`freshness_or_none` rather than a bare
    ``or 0.0``-style coercion: an unchecked claim must publish NO number, and a
    zero here would render as "0%" on the page instead of "Unknown".
    """

    return EvidenceCurrencyOut(
        current=currency.current,
        checked=currency.checked,
        stale=currency.stale,
        freshness=freshness_or_none(currency),
        age_days=_days(currency.age),
        window_days=_days(currency.window),
        oldest_fetched_at=currency.oldest_fetched_at,
        reason=currency.reason(),
    )


def _advanced_for(version: OfferVersion | None, currency: EvidenceCurrency) -> ConfidenceAdvanced:
    """The advanced block, suppressed when the evidence is not current.

    ``advanced`` carries the publish-time numeric score and the deterministic
    signal dict -- including a ``freshness`` signal that read ``1.0`` on
    five-year-expired evidence. Those are frozen values, so once the evidence
    behind them is no longer known to be current they are not merely stale, they
    are unsupported assertions of exactly the kind this work exists to stop.

    They are therefore withheld (``None``) rather than shown with a caveat, for
    the same reason :func:`confidence_label_for` collapses the label: this only
    ever removes unearned confidence. The reason is not lost -- it is carried, in
    plain language, by ``evidence_currency.reason``.
    """

    facts = _facts(version)
    if not currency.current:
        return ConfidenceAdvanced(score=None, signals=None)
    return ConfidenceAdvanced(
        score=_as_float(facts.get("confidence")),
        signals=dict(facts.get("confidence_signals"))
        if isinstance(facts.get("confidence_signals"), Mapping)
        else None,
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


def serialize_version(
    version: OfferVersion, currency: CurrencyContext = NO_CURRENCY
) -> OfferVersionOut:
    """Serialize one immutable offer version (history / current view)."""

    classification = _classification(version)
    verdict = currency.for_version(version.id)
    return OfferVersionOut(
        id=version.id,
        version_number=version.version_number,
        zero_cost_class=version.zero_cost_class,
        confidence_label=_label_for(version, verdict),
        reasons=_string_list(classification.get("reasons")),
        content_hash=version.content_hash,
        created_at=version.created_at,
        evidence_currency=_currency_out(verdict),
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


def _published_versions(provider: Provider) -> list[OfferVersion]:
    """Every published offer's current version for this provider."""

    versions = []
    for service in provider.services:
        for offer in service.offers:
            version = queries.latest_version(offer)
            if version is not None:
                versions.append(version)
    return versions


def _provider_scores(
    provider: Provider, currency: CurrencyContext = NO_CURRENCY
) -> tuple[float | None, float | None]:
    """Provider completeness / freshness.

    Freshness is now recomputed at READ time from evidence currency, not read
    back from a publish-time snapshot. The stored ``freshness_score`` column is
    still honoured if a future pipeline ever populates it (measured: nothing in
    the application writes it today, so every real provider takes the branch
    below), but the fallback no longer averages the frozen
    ``confidence_signals.freshness`` -- that is the value that reported ``1.0``
    for evidence which had expired years earlier.

    ``None`` -- never ``0.0`` -- when nothing about currency could be
    established: an absent measurement is not a bad score, and the web formatter
    renders the two very differently.

    Completeness is deliberately unchanged. It measures how much of the offer we
    captured, which does not decay with the calendar; only freshness does.
    """

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
            if comp is not None:
                comp_values.append(comp)
            live = freshness_or_none(currency.for_version(version.id))
            if live is not None:
                fresh_values.append(live)

    if completeness is None and comp_values:
        completeness = round(fmean(comp_values), 4)
    if freshness is None and fresh_values:
        freshness = round(fmean(fresh_values), 4)
    return completeness, freshness


def _counts(provider: Provider) -> tuple[int, int]:
    service_count = len(provider.services)
    published = sum(1 for s in provider.services for o in s.offers if queries.is_published(o))
    return service_count, published


def serialize_provider_summary(
    provider: Provider, currency: CurrencyContext = NO_CURRENCY
) -> ProviderSummary:
    completeness, freshness = _provider_scores(provider, currency)
    service_count, published = _counts(provider)
    rollup = currency.for_versions([v.id for v in _published_versions(provider)])
    return ProviderSummary(
        slug=provider.slug,
        name=provider.name,
        type=provider.type,
        source_health=provider.source_health,
        completeness=completeness,
        freshness=freshness,
        service_count=service_count,
        published_offer_count=published,
        evidence_currency=_currency_out(rollup),
    )


def serialize_provider_detail(
    provider: Provider, currency: CurrencyContext = NO_CURRENCY
) -> ProviderDetail:
    completeness, freshness = _provider_scores(provider, currency)
    service_count, published = _counts(provider)
    domains = provider.official_domains if isinstance(provider.official_domains, list) else []
    rollup = currency.for_versions([v.id for v in _published_versions(provider)])
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
        evidence_currency=_currency_out(rollup),
    )


def serialize_category_states(
    provider: Provider,
    cat_map: Mapping[int, Category],
    currency: CurrencyContext = NO_CURRENCY,
) -> CategoryStatesResponse:
    """Group the provider's published offers by category -> service -> offer state."""

    def _offer_state(offer) -> OfferState:
        version = queries.latest_version(offer)
        verdict = currency.for_version(version.id if version is not None else None)
        return OfferState(
            offer_id=offer.id,
            offer_type=offer.offer_type,
            zero_cost_class=offer.zero_cost_class,
            confidence_label=_label_for(version, verdict),
            status=offer.status,
            evidence_currency=_currency_out(verdict),
        )

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
            offers=[_offer_state(offer) for offer in published_offers],
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
    provider: Provider,
    cat_map: Mapping[int, Category],
    currency: CurrencyContext = NO_CURRENCY,
) -> list[OfferSummary]:
    summaries: list[OfferSummary] = []
    for service in provider.services:
        for offer in service.offers:
            if not queries.is_published(offer):
                continue
            version = queries.latest_version(offer)
            verdict = currency.for_version(version.id if version is not None else None)
            summaries.append(
                OfferSummary(
                    offer_id=offer.id,
                    service_id=service.id,
                    service_name=service.canonical_name,
                    category=_category_ref(service.category_id, cat_map),
                    offer_type=offer.offer_type,
                    zero_cost_class=offer.zero_cost_class,
                    status=offer.status,
                    confidence_label=_label_for(version, verdict),
                    current_version_number=version.version_number if version else None,
                    evidence_currency=_currency_out(verdict),
                )
            )
    summaries.sort(key=lambda s: s.offer_id)
    return summaries


def serialize_offer_detail(
    offer: Offer,
    cat_map: Mapping[int, Category],
    currency: CurrencyContext = NO_CURRENCY,
) -> OfferDetail:
    version = queries.latest_version(offer)
    service = offer.service
    provider = service.provider
    classification = _classification(version)
    verdict = currency.for_version(version.id if version is not None else None)

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
        current_version=serialize_version(version, currency) if version else None,
        reasons=_string_list(classification.get("reasons")),
        blocking_conditions=_string_list(classification.get("blocking_conditions")),
        quotas=quotas,
        confidence_label=_label_for(version, verdict),
        completeness=_signal(version, "completeness"),
        # Read-time freshness, not the publish-time snapshot. This is the field
        # that reported 1.0 on five-year-expired evidence.
        freshness=freshness_or_none(verdict),
        advanced=_advanced_for(version, verdict),
        evidence_currency=_currency_out(verdict),
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
    offer: Offer,
    evidence_rows: Sequence[Evidence],
    currency: CurrencyContext = NO_CURRENCY,
) -> OfferEvidenceResponse:
    version = queries.latest_version(offer)
    verdict = currency.for_version(version.id if version is not None else None)
    return OfferEvidenceResponse(
        offer_id=offer.id,
        offer_version_id=version.id if version else None,
        confidence_label=_label_for(version, verdict),
        advanced=_advanced_for(version, verdict),
        evidence_currency=_currency_out(verdict),
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
    currency: CurrencyContext = NO_CURRENCY,
) -> OfferHistoryResponse:
    return OfferHistoryResponse(
        offer_id=offer_id,
        versions=[serialize_version(v, currency) for v in versions],
        change_events=[serialize_change_event(e) for e in change_events],
    )


# --------------------------------------------------------------------------- #
# F006 slice 1 - search                                                       #
# --------------------------------------------------------------------------- #

#: The zero-cost class that proves a genuinely-free offer (drives the coverage
#: matrix ``verified_free`` state).
_FREE_CLASS = "Z0_TRUE_FREE"


def _search_result_item(
    offer: Offer,
    cat_map: Mapping[int, Category],
    currency: CurrencyContext = NO_CURRENCY,
) -> SearchResultItem:
    service = offer.service
    provider = service.provider
    version = queries.latest_version(offer)
    verdict = currency.for_version(version.id if version is not None else None)
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
        confidence_label=_label_for(version, verdict),
        current_version_number=version.version_number if version else None,
        evidence_currency=_currency_out(verdict),
    )


def serialize_search_response(
    page: SearchPage,
    params: SearchParams,
    cat_map: Mapping[int, Category],
    currency: CurrencyContext = NO_CURRENCY,
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
            evidence_current=params.evidence_current,
        ),
        page=page.page,
        page_size=page.page_size,
        total_results=page.total,
        total_pages=total_pages,
        results=[_search_result_item(o, cat_map, currency) for o in page.offers],
    )


# --------------------------------------------------------------------------- #
# F006 slice 1 - category coverage matrix                                     #
# --------------------------------------------------------------------------- #


def serialize_category_matrix(
    providers: Sequence[Provider],
    cat_map: Mapping[int, Category],
    context: queries.CoverageSignalContext | None = None,
    currency: CurrencyContext = NO_CURRENCY,
) -> CategoryMatrixResponse:
    """Cross the canonical 14-category taxonomy with each provider's coverage.

    Every pair reports three things: the human ``declared_state`` (from
    ``provider_category_coverage``), the ``derived_state`` computed on demand
    from published offers by :func:`app.read_api.coverage.derive_coverage_state`,
    and the ``state`` to display.

    There is deliberately **no** inference of ``not_offered``. Until F008 slice
    S2 this function guessed ``not_offered`` whenever a category had zero
    published offers, which conflated "we have not verified this" with "the
    provider does not offer it". A pair with no declaration now reports
    ``unknown``, and ``not_offered`` can only come from a declaration that states
    why. A published service whose category is absent or non-canonical is still
    not guessed into a category -- it is rolled up honestly into a per-provider
    ``uncategorized`` tally.

    The counts get a clock too (F008 slice S7)
    ------------------------------------------
    A count is a claim. Before this slice the offer *counts* on this surface were
    frozen against the calendar while the *states* beside them were not:
    measured across a one-second staleness boundary, ``state`` and
    ``derived_state`` moved and ``free_offer_count`` did not, so a cell could
    report ``state="stale"`` and "1 truly free" at the same moment.

    ``free_offer_count`` keeps its exact meaning and value, and
    ``current_free_offer_count`` is reported *beside* it, because both directions
    of error matter: overstating a free count asserts something unsupported, and
    silently shrinking it hides offers that really are free. An offer's claim is
    assessed on its **latest** version, matching every other catalogue surface
    (:func:`serialize_provider_summary`, :func:`serialize_category_states`).

    The bucket flag follows the latest version too
    ----------------------------------------------
    ``has_stale_evidence`` is a *bucket-wide* flag driving ``derived_state``, and
    it is coarse along two axes that are easy to conflate:

    * **across offers** -- one offer whose *latest* version is stale marks the
      whole cell. That is what bucket-wide means. It is deliberate, and it is
      unchanged here.
    * **across versions of one offer** -- until this slice a *superseded*
      version could mark the cell. That is not coarseness but a category error:
      the offer's claim rests on its latest version, so an ancestor's expiry
      says nothing about the claim actually being made.

    S7 recorded the second axis as deliberately untouched. It is touched now,
    because its cost lands in the direction that leaves no trace on the page.
    :func:`~app.read_api.coverage.derive_coverage_state` returns ``stale``
    *before* it can return ``verified_free``, so a single expired ancestor
    withheld the free badge from **every** offer in the bucket -- and a
    wrongly-withheld free offer is a defect of the same severity as a
    wrongly-asserted one. Nor was the old rule conservative in the sense
    ``fetch_stale_offer_version_ids`` claims for itself: overstating staleness
    admits uncertainty about *a current claim*, whereas a superseded version
    carries no uncertainty about the current claim at all.

    An ancestor is discharged only by POSITIVE currency
    ---------------------------------------------------
    The rule is emphatically **not** "the latest id is absent from the stale
    set". ``fetch_stale_offer_version_ids`` reports only versions it found
    ``stale``; a version whose evidence has no checkable fetch time is
    :data:`~app.read_api.currency.UNCHECKED` and is therefore *absent from that
    set for the opposite reason*. Reading absence as permission would let "we
    could not look at all" promote a bucket to ``verified_free`` -- an
    unsupported free claim on a public badge, which is the one thing this
    product may never ship.

    So the ancestor's staleness is discharged only by
    :func:`~app.read_api.currency.is_publishable_free_claim` -- the same
    predicate the counts above use, which fails closed on *both* shapes of
    non-currency. Two consequences worth naming: this rule can only ever
    **clear** a flag the all-versions rule set, never set one it left clear; and
    under :data:`~app.read_api.currency.NO_CURRENCY` every anchor is
    ``UNCHECKED``, so an un-clocked caller degrades to exactly the pre-slice
    all-versions behaviour instead of silently acquiring the more permissive one.

    Sharing ``latest_is_current`` with the counts also removes a
    self-contradiction: one cell could report ``evidence_currency`` current (a
    rollup over latest ids) and ``derived_state`` stale (a scan over every id) in
    the same response.
    """

    ordered_providers = sorted(providers, key=lambda p: p.slug)
    declarations = context.declarations if context is not None else {}
    conflicted = context.conflicted_services if context is not None else frozenset()
    stale_versions = context.stale_offer_version_ids if context is not None else frozenset()

    # (provider_slug, canonical_slug|None) -> tallies + derivation flags
    tally: dict[tuple[str, str | None], list[int]] = {}
    flags: dict[tuple[str, str | None], list[bool]] = {}
    # (provider_slug, canonical_slug|None) -> the latest-version id of every
    # published offer in the bucket, for the least-current rollup.
    bucket_versions: dict[tuple[str, str | None], list[int | None]] = {}
    for provider in ordered_providers:
        for service in provider.services:
            category = cat_map.get(service.category_id) if service.category_id else None
            slug = category.slug if category and is_canonical_slug(category.slug) else None
            service_conflicted = (provider.slug, service.canonical_name) in conflicted
            for offer in service.offers:
                if not queries.is_published(offer):
                    continue
                key = (provider.slug, slug)
                latest = queries.latest_version(offer)
                latest_id = latest.id if latest is not None else None
                # The one predicate that fails closed on BOTH shapes of
                # non-currency -- expired, and "we could not look at all".
                latest_is_current = is_publishable_free_claim(currency.for_version(latest_id))
                bucket_versions.setdefault(key, []).append(latest_id)
                bucket = tally.setdefault(key, [0, 0, 0, 0])
                bucket[0] += 1
                if offer.zero_cost_class == _FREE_CLASS:
                    bucket[1] += 1
                    # A missing clock therefore yields 0, never the whole tally.
                    if latest_is_current:
                        bucket[3] += 1
                elif offer.zero_cost_class in (None, coverage.UNCLASSIFIED_ZERO_COST_CLASS):
                    bucket[2] += 1
                flag = flags.setdefault(key, [False, False])
                flag[0] = flag[0] or service_conflicted
                flag[1] = flag[1] or (
                    any(v.id in stale_versions for v in offer.versions) and not latest_is_current
                )

    rows: list[CategoryMatrixRow] = []
    for taxon in CATEGORY_TAXONOMY:
        coverages: list[ProviderCoverage] = []
        for provider in ordered_providers:
            key = (provider.slug, taxon.slug)
            published, free, unclassified, current_free = tally.get(key, [0, 0, 0, 0])
            conflict_flag, stale_flag = flags.get(key, [False, False])
            signals = coverage.CoverageSignals(
                published_offer_count=published,
                free_offer_count=free,
                unclassified_offer_count=unclassified,
                has_pending_contradiction=conflict_flag,
                has_stale_evidence=stale_flag,
            )
            derived = coverage.derive_coverage_state(signals)
            declaration = declarations.get((provider.id, taxon.slug))
            declared_state = declaration.state if declaration is not None else None
            coverages.append(
                ProviderCoverage(
                    provider_slug=provider.slug,
                    provider_name=provider.name,
                    state=coverage.effective_state(declared_state, derived),
                    declared_state=declared_state,
                    derived_state=derived,
                    mismatch=coverage.is_material_mismatch(declared_state, derived),
                    rationale=declaration.rationale if declaration is not None else None,
                    evidence_url=declaration.evidence_url if declaration is not None else None,
                    published_offer_count=published,
                    free_offer_count=free,
                    current_free_offer_count=current_free,
                    evidence_currency=_currency_out(
                        currency.for_versions(bucket_versions.get(key, []))
                    ),
                )
            )
        rows.append(
            CategoryMatrixRow(
                ordinal=taxon.ordinal, slug=taxon.slug, name=taxon.name, providers=coverages
            )
        )

    uncategorized: list[UncategorizedCoverage] = []
    for provider in ordered_providers:
        key = (provider.slug, None)
        published, free, _unclassified, current_free = tally.get(key, [0, 0, 0, 0])
        if published == 0:
            continue
        uncategorized.append(
            UncategorizedCoverage(
                provider_slug=provider.slug,
                provider_name=provider.name,
                published_offer_count=published,
                free_offer_count=free,
                current_free_offer_count=current_free,
                evidence_currency=_currency_out(
                    currency.for_versions(bucket_versions.get(key, []))
                ),
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


def _compare_offer(
    offer: Offer,
    cat_map: Mapping[int, Category],
    currency: CurrencyContext = NO_CURRENCY,
) -> CompareOffer:
    service = offer.service
    provider = service.provider
    version = queries.latest_version(offer)
    classification = _classification(version)
    quotas = version.quotas if version else []
    evidence_count = len(version.evidence) if version else 0
    verdict = currency.for_version(version.id if version is not None else None)

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
        confidence_label=_label_for(version, verdict),
        completeness=_signal(version, "completeness"),
        freshness=freshness_or_none(verdict),
        evidence_count=evidence_count,
        advanced=_advanced_for(version, verdict),
        evidence_currency=_currency_out(verdict),
    )


def serialize_compare(
    offer_ids: Sequence[int],
    offers: Sequence[Offer],
    cat_map: Mapping[int, Category],
    currency: CurrencyContext = NO_CURRENCY,
) -> CompareResponse:
    """Serialize a bounded set of published offers into a side-by-side comparison.

    ``offers`` is expected to already be resolved + published (the router rejects
    unknown/unpublished ids with 404) and presented in the caller's requested
    order; ``offer_ids`` echoes that requested order for the client.
    """

    return CompareResponse(
        offer_ids=list(offer_ids),
        offers=[_compare_offer(o, cat_map, currency) for o in offers],
    )
