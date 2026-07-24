"""Response schemas + deterministic serializer for the adviser (F006 slice 3).

Pydantic response models for ``POST /adviser/recommend`` plus
:func:`build_response`, which turns a :class:`~app.adviser.recommend.RecommendationResult`
into that payload using the templated explanations in :mod:`app.adviser.explain`.

Exactness rule: every numeric amount that participates in a fit/headroom decision
is serialized as a **string** (``str(Decimal)``) so the exact value survives JSON
without a float round-trip. Portability scores are likewise strings. Fields that
may be genuinely unknown are ``Optional`` and surfaced as ``null`` -- never a
guessed value.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel

from . import explain
from .quota_math import DemandFit, OfferFit
from .recommend import (
    ComponentRecommendation,
    ImpossibleResolution,
    NotFreeOption,
    RecommendationResult,
    ReductionStep,
    SelfHostingOption,
)
from .select import OfferCandidate


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class EvidenceRefOut(BaseModel):
    title: str | None = None
    url: str | None = None
    official: bool = False


class PortabilityOut(BaseModel):
    score: str
    label: str
    lock_in_label: str
    deployment_model: str
    positive_traits: list[str] = []
    negative_traits: list[str] = []
    unknown_traits: list[str] = []
    basis: list[str] = []
    exit_plan: list[str] = []


class DemandFitOut(BaseModel):
    metric: str
    covered: bool
    boundary: bool = False
    demand_amount: str
    demand_unit: str
    demand_period: str | None = None
    matched_metric: str | None = None
    canonical_unit: str | None = None
    demand_canonical: str | None = None
    quota_canonical: str | None = None
    headroom: str | None = None
    reason: str


class OfferRefOut(BaseModel):
    provider_slug: str
    provider_name: str
    service_name: str
    offer_id: int
    zero_cost_class: str
    confidence_label: str


class ComponentOut(BaseModel):
    requirement_index: int
    category: str
    label: str | None = None
    offer: OfferRefOut
    reduced: bool = False
    demands: list[DemandFitOut] = []
    quota_math: list[str] = []
    z0_safety: list[str] = []
    portability: PortabilityOut
    evidence: list[EvidenceRefOut] = []
    explanation: list[str] = []


class ReductionOut(BaseModel):
    metric: str
    original_amount: str
    original_unit: str
    reduced_amount: str | None = None
    feasible: bool
    reason: str


class SelfHostingOut(BaseModel):
    building_block: OfferRefOut
    host: OfferRefOut | None = None
    note: str


class ImpossibleOut(BaseModel):
    requirement_index: int
    category: str
    label: str | None = None
    blocking_reason: str
    closest: OfferRefOut | None = None
    reductions: list[ReductionOut] = []
    recalculated: ComponentOut | None = None
    self_hosting: list[SelfHostingOut] = []
    steps: list[str] = []


class NotFreeOut(BaseModel):
    requirement_index: int
    category: str
    offer: OfferRefOut
    fits: bool
    note: str


class NotFreeSectionOut(BaseModel):
    label: str = "These options are NOT $0 and are not part of the recommendation."
    options: list[NotFreeOut] = []


class RecommendationResponse(BaseModel):
    workload_name: str | None = None
    priorities: list[str] = []
    fully_zero_cost: bool
    zero_cost_proof: list[str] = []
    architecture: list[ComponentOut] = []
    impossible: list[ImpossibleOut] = []
    not_free_section: NotFreeSectionOut = NotFreeSectionOut()


# --------------------------------------------------------------------------- #
# Serialization                                                               #
# --------------------------------------------------------------------------- #


def _offer_ref(candidate: OfferCandidate) -> OfferRefOut:
    return OfferRefOut(
        provider_slug=candidate.provider_slug,
        provider_name=candidate.provider_name,
        service_name=candidate.service_name,
        offer_id=candidate.offer_id,
        zero_cost_class=candidate.zero_cost_class,
        confidence_label=candidate.confidence_label,
    )


def _portability_out(candidate: OfferCandidate) -> PortabilityOut:
    p = candidate.portability
    return PortabilityOut(
        score=str(p.score),
        label=p.label,
        lock_in_label=p.lock_in_label,
        deployment_model=p.deployment_model,
        positive_traits=list(p.positive_traits),
        negative_traits=list(p.negative_traits),
        unknown_traits=list(p.unknown_traits),
        basis=list(p.basis),
        exit_plan=list(p.exit_plan),
    )


def _demand_fit_out(df: DemandFit) -> DemandFitOut:
    return DemandFitOut(
        metric=df.metric,
        covered=df.covered,
        boundary=df.is_boundary,
        demand_amount=str(df.demand_amount),
        demand_unit=df.demand_unit,
        demand_period=df.demand_period,
        matched_metric=df.matched_metric,
        canonical_unit=df.canonical_unit,
        demand_canonical=_dec(df.demand_canonical),
        quota_canonical=_dec(df.quota_canonical),
        headroom=_dec(df.headroom),
        reason=df.reason,
    )


def _component_out(component: ComponentRecommendation) -> ComponentOut:
    candidate = component.candidate
    fit: OfferFit = component.offer_fit
    portability = _portability_out(candidate)
    explanation: list[str] = []
    explanation.append(explain.component_summary(component))
    explanation.extend(explain.quota_math_lines(fit))
    explanation.extend(explain.z0_safety_lines(candidate))
    explanation.extend(explain.portability_lines(candidate))
    return ComponentOut(
        requirement_index=component.requirement_index,
        category=component.category,
        label=component.label,
        offer=_offer_ref(candidate),
        reduced=component.reduced,
        demands=[_demand_fit_out(df) for df in fit.demand_fits],
        quota_math=explain.quota_math_lines(fit),
        z0_safety=explain.z0_safety_lines(candidate),
        portability=portability,
        evidence=[EvidenceRefOut(**ref) for ref in explain.evidence_refs(candidate)],
        explanation=explanation,
    )


def _reduction_out(step: ReductionStep) -> ReductionOut:
    return ReductionOut(
        metric=step.metric,
        original_amount=str(step.original_amount),
        original_unit=step.original_unit,
        reduced_amount=_dec(step.reduced_amount),
        feasible=step.feasible,
        reason=step.reason,
    )


def _self_hosting_out(option: SelfHostingOption) -> SelfHostingOut:
    return SelfHostingOut(
        building_block=_offer_ref(option.building_block),
        host=_offer_ref(option.host) if option.host is not None else None,
        note=option.note,
    )


def _impossible_out(resolution: ImpossibleResolution) -> ImpossibleOut:
    return ImpossibleOut(
        requirement_index=resolution.requirement_index,
        category=resolution.category,
        label=resolution.label,
        blocking_reason=resolution.blocking_reason,
        closest=(
            _offer_ref(resolution.closest_candidate)
            if resolution.closest_candidate is not None
            else None
        ),
        reductions=[_reduction_out(s) for s in resolution.reductions],
        recalculated=(
            _component_out(resolution.recalculated) if resolution.recalculated is not None else None
        ),
        self_hosting=[_self_hosting_out(o) for o in resolution.self_hosting],
        steps=explain.impossible_lines(resolution),
    )


def _not_free_out(option: NotFreeOption) -> NotFreeOut:
    return NotFreeOut(
        requirement_index=option.requirement_index,
        category=option.category,
        offer=_offer_ref(option.candidate),
        fits=option.offer_fit.fits,
        note=(
            f"{option.candidate.zero_cost_class}: excluded from the $0 recommendation; "
            "shown only for awareness."
        ),
    )


def build_response(result: RecommendationResult) -> RecommendationResponse:
    """Serialize a :class:`RecommendationResult` into the API response payload."""

    return RecommendationResponse(
        workload_name=result.workload_name,
        priorities=list(result.priorities),
        fully_zero_cost=result.fully_zero_cost,
        zero_cost_proof=explain.zero_cost_proof(result),
        architecture=[_component_out(c) for c in result.components],
        impossible=[_impossible_out(r) for r in result.impossible],
        not_free_section=NotFreeSectionOut(options=[_not_free_out(o) for o in result.not_free]),
    )


__all__: Sequence[str] = (
    "EvidenceRefOut",
    "PortabilityOut",
    "DemandFitOut",
    "OfferRefOut",
    "ComponentOut",
    "ReductionOut",
    "SelfHostingOut",
    "ImpossibleOut",
    "NotFreeOut",
    "NotFreeSectionOut",
    "RecommendationResponse",
    "build_response",
)
