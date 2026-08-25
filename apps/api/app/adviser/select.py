"""Published-catalogue selection for the adviser (F006 slice 3).

Gathers the *published* offers the adviser is allowed to consider and partitions
them by zero-cost class, with a hard Z0-safety cross-check. Read-only and
published-only:

* Only offers that are published (at least one immutable ``offer_version``) are
  read. The ``candidate`` and quarantined ``discovery_candidate`` tables are
  **never** queried, so community/pre-publication data can never enter a
  recommendation.
* The zero-cost class is not trusted blindly. For every offer the shared
  classify engine (:func:`app.classify.classify_offer`) is re-run over the
  persisted material facts and its verdict is compared to the persisted
  ``zero_cost_class``. **Only when they agree** is the offer usable; a
  disagreement is a contradiction and the offer is excluded (fail closed), never
  recommended. The adviser therefore never re-derives Z0 itself -- it reuses the
  engine -- and never recommends an unknown/contradictory offer.

The result partitions agreeing offers into: ``z0`` (the only offers that may
enter a guaranteed-$0 architecture), ``z3`` (self-hostable building blocks held
for the self-hosting fallback), ``not_free`` (Z1/Z2, surfaced only in the
separate "not $0" section), and ``stale`` (offers that classify Z0 but whose
official evidence is no longer known to be current). UNKNOWN and contradictory
offers are excluded entirely.

Evidence currency is a separate axis from classification
--------------------------------------------------------
The classify cross-check above compares **two classifications** of the same
persisted facts. Both sides read the same frozen ``material_facts``, so they
agree perfectly on evidence that expired years ago -- a contradiction is never
raised, and the cross-check must not be mistaken for a currency guard. Whether
the evidence still *supports* the classification is an orthogonal question,
answered by :mod:`app.read_api.currency` against a caller-supplied ``now``.

A Z0 offer whose evidence is not current is routed to the ``stale`` partition
rather than to ``excluded``, so the reason survives into the recommendation and
the adviser can say *why* a requirement could not be met on a $0 guarantee. It is
deliberately not dropped: suppressing a free offer outright is its own defect,
and the offer still surfaces as the closest candidate with a stated reason.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.classify import classify_offer
from app.classify.engine import (
    Z0_TRUE_FREE,
    Z1_BILLING_EXPOSURE,
    Z2_TEMPORARY_OR_CONDITIONAL,
    Z3_SELF_HOSTED_BUILDING_BLOCK,
)
from app.models.domain import (
    Category,
    Offer,
    OfferVersion,
    RegionAvailability,
    Service,
)
from app.read_api.confidence import confidence_label
from app.read_api.currency import (
    ANCHOR_OFFER_VERSION,
    UNCHECKED,
    EvidenceCurrency,
    confidence_label_for,
    currency_for,
    is_publishable_free_claim,
)
from app.read_api.queries import fetch_evidence_currency, is_published, latest_version

from .portability import PortabilityAssessment, assess_portability


@dataclass(frozen=True)
class OfferCandidate:
    """One published offer, ready for deterministic evaluation.

    Carries everything the adviser needs without further DB access: the resolved
    provider/service identity, the offer's canonical category slug (``None`` when
    the service is uncategorised -- never guessed), the *agreed* zero-cost class,
    the current version's quotas and evidence, the classify engine's reasons
    (the Z0-safety rationale, deterministic from persisted facts), and the
    deterministic portability assessment.
    """

    offer_id: int
    provider_slug: str
    provider_name: str
    service_name: str
    category_slug: str | None
    deployment_model: str
    zero_cost_class: str
    persisted_class: str
    engine_class: str
    version_id: int
    version_number: int
    confidence_label: str
    reasons: tuple[str, ...]
    blocking_conditions: tuple[str, ...]
    quotas: tuple[object, ...]
    evidence: tuple[object, ...]
    region_rows: tuple[object, ...]
    portability: PortabilityAssessment
    commercial_use_allowed: bool | None
    personal_use_allowed: bool | None
    requires_card: bool | None
    has_paid_dependencies: bool | None
    #: Whether the official evidence behind this offer is still inside its
    #: refresh window. Defaults to ``UNCHECKED`` so a caller that has not been
    #: threaded a clock fails closed ("cannot assert currency") rather than
    #: re-acquiring the old always-current behaviour.
    evidence_currency: EvidenceCurrency = UNCHECKED

    @property
    def evidence_is_current(self) -> bool:
        """May a free claim resting on this offer still be repeated?"""

        return is_publishable_free_claim(self.evidence_currency)

    def sort_key(self) -> tuple:
        """A deterministic tie-break key (provider slug, then offer id)."""

        return (self.provider_slug, self.offer_id)


@dataclass(frozen=True)
class CandidatePool:
    """Published offers partitioned by the agreed zero-cost class."""

    z0: tuple[OfferCandidate, ...]
    z3: tuple[OfferCandidate, ...]
    not_free: tuple[OfferCandidate, ...]
    excluded: tuple[OfferCandidate, ...] = field(default_factory=tuple)
    #: Offers that classify Z0_TRUE_FREE but whose evidence is not current. Held
    #: separately from ``excluded`` so the recommendation can explain the refusal
    #: instead of the offer silently vanishing.
    stale: tuple[OfferCandidate, ...] = field(default_factory=tuple)


_CONFIDENCE_RANK: Mapping[str, int] = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


def confidence_rank(label: str) -> int:
    """Rank a confidence label for ordering (lower is better)."""

    return _CONFIDENCE_RANK.get(label, _CONFIDENCE_RANK["unknown"])


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _confidence_for(version: OfferVersion | None) -> str:
    facts = version.material_facts if version is not None else None
    if not isinstance(facts, Mapping):
        return "unknown"
    gate = facts.get("gate") if isinstance(facts.get("gate"), Mapping) else {}
    return confidence_label(
        _as_float(facts.get("confidence")),
        automatic_threshold=_as_float(gate.get("automatic_threshold")),
        uncertain_threshold=_as_float(gate.get("uncertain_threshold")),
    )


def _published_offers(session: Session) -> Sequence[Offer]:
    """All published offers with the full evaluation graph eager-loaded.

    Never touches ``candidate`` / ``discovery_candidate``: only the published
    ``offer`` graph is read.
    """

    stmt = select(Offer).options(
        selectinload(Offer.service).selectinload(Service.provider),
        selectinload(Offer.versions).selectinload(OfferVersion.quotas),
        selectinload(Offer.versions).selectinload(OfferVersion.evidence),
    )
    offers = list(session.execute(stmt).scalars().unique())
    return [offer for offer in offers if is_published(offer)]


def _category_slugs(session: Session, offers: Sequence[Offer]) -> Mapping[int, str]:
    ids = {
        offer.service.category_id
        for offer in offers
        if offer.service is not None and offer.service.category_id is not None
    }
    if not ids:
        return {}
    stmt = select(Category).where(Category.id.in_(ids))
    return {c.id: c.slug for c in session.execute(stmt).scalars().unique()}


def _region_index(session: Session) -> Mapping[tuple[int | None, int | None], list[object]]:
    """Index region availability rows by ``(provider_id, offer_id)``.

    ``offer_id`` may be ``None`` for a provider-wide row. Read-only.
    """

    index: dict[tuple[int | None, int | None], list[object]] = {}
    for row in session.execute(select(RegionAvailability)).scalars().unique():
        index.setdefault((row.provider_id, row.offer_id), []).append(row)
    return index


def _region_rows_for(
    offer: Offer,
    index: Mapping[tuple[int | None, int | None], list[object]],
) -> tuple[object, ...]:
    provider_id = offer.service.provider.id if offer.service and offer.service.provider else None
    rows: list[object] = []
    rows.extend(index.get((provider_id, offer.id), []))
    rows.extend(index.get((provider_id, None), []))
    return tuple(rows)


def _evidence_for(version: OfferVersion | None) -> tuple[object, ...]:
    if version is None:
        return ()
    return tuple(sorted(version.evidence, key=lambda e: e.id))


def build_candidate(
    offer: Offer,
    category_slugs: Mapping[int, str],
    region_index: Mapping[tuple[int | None, int | None], list[object]],
    currency_index: Mapping[tuple[str, int], EvidenceCurrency] | None = None,
) -> OfferCandidate | None:
    """Build an :class:`OfferCandidate` for one published offer, or ``None``.

    Returns ``None`` only when the offer has no service/provider (a malformed
    row that cannot be safely evaluated). The classify cross-check is applied
    here: ``engine_class`` is the engine's verdict, ``persisted_class`` the
    stored one, and ``zero_cost_class`` is the agreed value when they match (or
    ``UNKNOWN`` sentinel handling is left to :func:`gather_candidates`).

    ``currency_index`` supplies the evidence-currency verdict. Omitting it does
    **not** mean "current": the candidate resolves to ``UNCHECKED`` and is
    therefore not eligible for a $0 guarantee.
    """

    service = offer.service
    if service is None or service.provider is None:
        return None

    version = latest_version(offer)
    result = classify_offer(offer)
    engine_class = result.zero_cost_class
    persisted = offer.zero_cost_class

    category_slug = (
        category_slugs.get(service.category_id) if service.category_id is not None else None
    )

    currency = currency_for(
        ANCHOR_OFFER_VERSION,
        version.id if version is not None else None,
        currency_index,
    )

    return OfferCandidate(
        offer_id=offer.id,
        provider_slug=service.provider.slug,
        provider_name=service.provider.name,
        service_name=service.canonical_name,
        category_slug=category_slug,
        deployment_model=service.deployment_model,
        zero_cost_class=engine_class if engine_class == persisted else "CONTRADICTION",
        persisted_class=persisted,
        engine_class=engine_class,
        version_id=version.id if version is not None else 0,
        version_number=version.version_number if version is not None else 0,
        # A confidence score is frozen at publish time and cannot know its
        # evidence later expired, so the label is capped by what the evidence
        # still supports.
        confidence_label=confidence_label_for(_confidence_for(version), currency),
        reasons=tuple(result.reasons),
        blocking_conditions=tuple(result.blocking_conditions),
        quotas=tuple(version.quotas) if version is not None else (),
        evidence=_evidence_for(version),
        region_rows=_region_rows_for(offer, region_index),
        portability=assess_portability(service.deployment_model, service.portability_traits),
        commercial_use_allowed=offer.commercial_use_allowed,
        personal_use_allowed=offer.personal_use_allowed,
        requires_card=offer.requires_card,
        has_paid_dependencies=offer.has_paid_dependencies,
        evidence_currency=currency,
    )


def build_pool(
    offers: Sequence[Offer],
    category_slugs: Mapping[int, str],
    region_index: Mapping[tuple[int | None, int | None], list[object]],
    currency_index: Mapping[tuple[str, int], EvidenceCurrency] | None = None,
) -> CandidatePool:
    """Partition published ``offers`` into a :class:`CandidatePool` (pure).

    Two independent gates decide whether an offer may enter ``z0``:

    1. **Classification agreement.** The classify engine's verdict is compared to
       the persisted class; a disagreement is a contradiction and the offer is
       excluded (fail closed).
    2. **Evidence currency.** A Z0 verdict both sides agree on still only earns a
       $0 guarantee while its official evidence is inside its refresh window.
       Gate 1 cannot substitute for gate 2 -- both sides read the same frozen
       facts, so they agree perfectly on evidence that expired years ago.

    Only published offers (:func:`app.read_api.queries.is_published`) are
    considered, so this can be called directly with in-memory offers (e.g. the
    deterministic corpus) without a database session.
    """

    z0: list[OfferCandidate] = []
    z3: list[OfferCandidate] = []
    not_free: list[OfferCandidate] = []
    excluded: list[OfferCandidate] = []
    stale: list[OfferCandidate] = []

    for offer in offers:
        if not is_published(offer):
            continue
        candidate = build_candidate(offer, category_slugs, region_index, currency_index)
        if candidate is None:
            continue
        if candidate.engine_class != candidate.persisted_class:
            excluded.append(candidate)
            continue
        agreed = candidate.engine_class
        if agreed == Z0_TRUE_FREE:
            # Gate 2. A free claim whose support has expired (or was never
            # checkable) cannot back a guaranteed-$0 architecture.
            if candidate.evidence_is_current:
                z0.append(candidate)
            else:
                stale.append(candidate)
        elif agreed == Z3_SELF_HOSTED_BUILDING_BLOCK:
            z3.append(candidate)
        elif agreed in (Z1_BILLING_EXPOSURE, Z2_TEMPORARY_OR_CONDITIONAL):
            not_free.append(candidate)
        else:  # UNKNOWN -> excluded (fail closed)
            excluded.append(candidate)

    key = OfferCandidate.sort_key
    return CandidatePool(
        z0=tuple(sorted(z0, key=key)),
        z3=tuple(sorted(z3, key=key)),
        not_free=tuple(sorted(not_free, key=key)),
        excluded=tuple(sorted(excluded, key=key)),
        stale=tuple(sorted(stale, key=key)),
    )


def gather_candidates(session: Session, *, now: datetime | None = None) -> CandidatePool:
    """Read the published catalogue and partition it by agreed zero-cost class.

    Thin DB wrapper around :func:`build_pool`: reads the published offer graph
    (never ``candidate`` / ``discovery_candidate``) plus the category-slug,
    region-availability and evidence-currency indexes, then delegates the pure
    partition.

    ``now`` is the currency clock. It is a parameter rather than an internal
    ``datetime.now()`` so a test can place the catalogue at any instant without
    touching a stored timestamp, and so the adviser's clock is the same one its
    HTTP handler already uses.
    """

    moment = now or datetime.now(UTC)
    offers = _published_offers(session)
    category_slugs = _category_slugs(session, offers)
    region_index = _region_index(session)
    currency_index = fetch_evidence_currency(session, now=moment)
    return build_pool(offers, category_slugs, region_index, currency_index)


__all__: Sequence[str] = (
    "OfferCandidate",
    "CandidatePool",
    "confidence_rank",
    "build_candidate",
    "build_pool",
    "gather_candidates",
)
