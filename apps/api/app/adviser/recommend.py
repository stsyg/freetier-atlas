"""The deterministic adviser orchestrator (F006 slice 3).

Given a strict :class:`~app.adviser.schema.RecommendationRequest` and a
:class:`~app.adviser.select.CandidatePool` (the published catalogue partitioned
by agreed zero-cost class), :func:`recommend` produces a fully deterministic
:class:`RecommendationResult`.

Behaviour, in strict product order (docs/PRODUCT_REQUIREMENTS.md "Adviser
behaviour", D014):

1. For each requirement, match only Z0 offers that declare the requirement's
   canonical category *and* satisfy its constraints, then keep those whose quotas
   cover every demand with exact-Decimal headroom. When one or more fit, pick the
   single best by a **stable total ordering** so identical inputs always yield an
   identical choice: most headroom margin, then higher confidence, then higher
   portability, then provider slug, then offer id.
2. When a requirement has no fitting Z0 offer it is *blocking*, and the impossible
   order is followed **exactly**: (a) explain the blocking requirement, (b)
   compute the demand *reduction* that would fit the best available Z0 headroom,
   (c) *recalculate* selection under the reduced demand and show the resulting Z0
   architecture, then (d) offer *self-hosting* (Z3 building blocks on Z0 hosting).
3. Z1/Z2 options never enter the recommendation or the impossible order; they are
   collected into a separate, clearly-marked "not $0" section only.

No LLM, no network, no persistence: a pure function of its two arguments.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.classify.engine import Z0_TRUE_FREE

from .quota_math import DemandFit, OfferFit, evaluate_offer, reduced_demand_amount
from .schema import FIXED_PRIORITIES, Constraints, RecommendationRequest, Requirement
from .select import CandidatePool, OfferCandidate, confidence_rank

#: Canonical categories that can host a self-hosted (Z3) building block on $0
#: infrastructure. Used to place a self-hosting fallback on a Z0 host.
HOSTING_CATEGORIES: frozenset[str] = frozenset(
    {"compute-vms", "containers-app-hosting", "serverless-functions"}
)


@dataclass(frozen=True)
class _SimpleDemand:
    """A lightweight demand used for recalculation under reduced amounts."""

    metric: str
    amount: Decimal
    unit: str
    period: str | None


@dataclass(frozen=True)
class ComponentRecommendation:
    """A chosen Z0 offer that satisfies one requirement (possibly reduced)."""

    requirement_index: int
    category: str
    label: str | None
    candidate: OfferCandidate
    offer_fit: OfferFit
    reduced: bool = False
    reduced_demands: tuple[_SimpleDemand, ...] = ()


@dataclass(frozen=True)
class ReductionStep:
    """One demand's deterministic reduction toward the best available headroom."""

    metric: str
    original_amount: Decimal
    original_unit: str
    reduced_amount: Decimal | None
    feasible: bool
    reason: str


@dataclass(frozen=True)
class SelfHostingOption:
    """A Z3 self-hostable building block placed on a Z0 host."""

    building_block: OfferCandidate
    host: OfferCandidate | None
    note: str


@dataclass(frozen=True)
class ImpossibleResolution:
    """The ordered resolution of a blocking requirement (no fitting Z0 offer)."""

    requirement_index: int
    category: str
    label: str | None
    blocking_reason: str
    closest_candidate: OfferCandidate | None
    blocking_fits: tuple[DemandFit, ...]
    reductions: tuple[ReductionStep, ...]
    recalculated: ComponentRecommendation | None
    self_hosting: tuple[SelfHostingOption, ...]


@dataclass(frozen=True)
class NotFreeOption:
    """A Z1/Z2 offer relevant to a requested category (separate section only)."""

    requirement_index: int
    category: str
    candidate: OfferCandidate
    offer_fit: OfferFit


@dataclass(frozen=True)
class RecommendationResult:
    """The complete deterministic recommendation."""

    workload_name: str | None
    priorities: tuple[str, ...]
    fully_zero_cost: bool
    components: tuple[ComponentRecommendation, ...]
    impossible: tuple[ImpossibleResolution, ...] = field(default_factory=tuple)
    not_free: tuple[NotFreeOption, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# Matching helpers                                                            #
# --------------------------------------------------------------------------- #


def _category_matches(
    candidates: Sequence[OfferCandidate], requirement: Requirement
) -> list[OfferCandidate]:
    """Z0 offers that declare the requirement's canonical category.

    An uncategorised offer (``category_slug is None``) never matches -- the
    adviser does not guess a category.
    """

    return [c for c in candidates if c.category_slug == requirement.category]


def _constraint_ok(candidate: OfferCandidate, constraints: Constraints) -> tuple[bool, str]:
    """Check the non-quota constraints, failing closed on anything unconfirmed."""

    if constraints.commercial_use and candidate.commercial_use_allowed is not True:
        return False, (
            f"Offer '{candidate.service_name}' does not confirm commercial use is allowed."
        )
    if constraints.personal_use_ok is False and candidate.personal_use_allowed is not True:
        return False, (
            f"Offer '{candidate.service_name}' does not confirm the required usage rights."
        )
    if constraints.region:
        region = constraints.region.strip().lower()
        ok = any(
            getattr(r, "region_code", "").strip().lower() == region
            and getattr(r, "free_available", False)
            for r in candidate.region_rows
        )
        if not ok:
            return False, (f"No confirmed free availability for region '{constraints.region}'.")
    if constraints.residency:
        residency = constraints.residency.strip().lower()
        ok = any(
            (getattr(r, "residency", None) or "").strip().lower() == residency
            and getattr(r, "free_available", False)
            for r in candidate.region_rows
        )
        if not ok:
            return False, (f"No confirmed data residency '{constraints.residency}'.")
    return True, ""


def _eligible(
    candidates: Sequence[OfferCandidate], requirement: Requirement
) -> tuple[list[OfferCandidate], list[tuple[OfferCandidate, str]]]:
    """Partition category-matched candidates into constraint-eligible and rejected."""

    eligible: list[OfferCandidate] = []
    rejected: list[tuple[OfferCandidate, str]] = []
    for candidate in _category_matches(candidates, requirement):
        ok, reason = _constraint_ok(candidate, requirement.constraints)
        if ok:
            eligible.append(candidate)
        else:
            rejected.append((candidate, reason))
    return eligible, rejected


def _order_key(pair: tuple[OfferCandidate, OfferFit]) -> tuple:
    """Stable total ordering for fitting offers (best first).

    Most headroom margin, then higher confidence, then higher portability, then
    provider slug, then offer id -- a deterministic total order over distinct
    offers so identical inputs always select the same offer.
    """

    candidate, fit = pair
    margin = fit.min_headroom_ratio if fit.min_headroom_ratio is not None else Decimal(0)
    return (
        -margin,
        confidence_rank(candidate.confidence_label),
        -candidate.portability.score,
        candidate.provider_slug,
        candidate.offer_id,
    )


def _evaluate(
    candidates: Sequence[OfferCandidate], demands: Sequence[object]
) -> list[tuple[OfferCandidate, OfferFit]]:
    return [(c, evaluate_offer(demands, c.quotas)) for c in candidates]


# --------------------------------------------------------------------------- #
# Impossible-order steps                                                      #
# --------------------------------------------------------------------------- #


def _closest(
    non_fitting: Sequence[tuple[OfferCandidate, OfferFit]],
) -> tuple[OfferCandidate, OfferFit] | None:
    """The Z0 offer that comes closest to fitting (fewest blocking demands)."""

    if not non_fitting:
        return None

    def key(pair: tuple[OfferCandidate, OfferFit]) -> tuple:
        candidate, fit = pair
        blocking = len(fit.blocking)
        covered = sum(1 for df in fit.demand_fits if df.covered)
        return (blocking, -covered, candidate.provider_slug, candidate.offer_id)

    return min(non_fitting, key=key)


def _reduce(
    requirement: Requirement, closest: OfferCandidate
) -> tuple[tuple[ReductionStep, ...], tuple[_SimpleDemand, ...], bool]:
    """Compute the deterministic demand reduction that fits ``closest``'s headroom.

    Returns the per-demand reduction steps, the reduced demand set, and whether
    every blocking demand could be reduced (fail closed: a demand whose unit
    cannot be normalised yields no reduction and marks the reduction infeasible).
    """

    fit = evaluate_offer(requirement.demands, closest.quotas)
    steps: list[ReductionStep] = []
    reduced: list[_SimpleDemand] = []
    feasible = True

    for demand, df in zip(requirement.demands, fit.demand_fits, strict=True):
        if df.covered:
            reduced.append(_SimpleDemand(demand.metric, demand.amount, demand.unit, demand.period))
            continue
        amount = reduced_demand_amount(demand, closest.quotas)
        if amount is None or amount <= Decimal(0):
            feasible = False
            steps.append(
                ReductionStep(
                    metric=demand.metric,
                    original_amount=demand.amount,
                    original_unit=demand.unit,
                    reduced_amount=None,
                    feasible=False,
                    reason=(
                        f"Demand for '{demand.metric}' cannot be reduced to fit "
                        f"'{closest.service_name}' (no comparable, known-unit quota)."
                    ),
                )
            )
            reduced.append(_SimpleDemand(demand.metric, demand.amount, demand.unit, demand.period))
        else:
            steps.append(
                ReductionStep(
                    metric=demand.metric,
                    original_amount=demand.amount,
                    original_unit=demand.unit,
                    reduced_amount=amount,
                    feasible=True,
                    reason=(
                        f"Reduce '{demand.metric}' from {demand.amount} to {amount} "
                        f"{demand.unit} to fit the {closest.service_name} free quota."
                    ),
                )
            )
            reduced.append(_SimpleDemand(demand.metric, amount, demand.unit, demand.period))

    return tuple(steps), tuple(reduced), feasible


def _self_hosting(pool: CandidatePool, requirement: Requirement) -> tuple[SelfHostingOption, ...]:
    """Z3 building blocks in the requirement's category, placed on a Z0 host."""

    building_blocks = [c for c in pool.z3 if c.category_slug == requirement.category]
    if not building_blocks:
        return ()

    hosts = sorted(
        (c for c in pool.z0 if c.category_slug in HOSTING_CATEGORIES),
        key=OfferCandidate.sort_key,
    )
    host = hosts[0] if hosts else None

    options: list[SelfHostingOption] = []
    for block in sorted(building_blocks, key=OfferCandidate.sort_key):
        if host is not None:
            note = (
                f"Run the self-hosted '{block.service_name}' on the Z0 host "
                f"'{host.service_name}' ({host.provider_name}); the combination stays $0 "
                "as long as the host's free quotas are respected."
            )
        else:
            note = (
                f"Self-host '{block.service_name}' on any verified Z0 host; no published "
                "Z0 hosting offer is currently available to name automatically."
            )
        options.append(SelfHostingOption(building_block=block, host=host, note=note))
    return tuple(options)


def _blocking_reason(
    requirement: Requirement,
    eligible_count: int,
    rejected: Sequence[tuple[OfferCandidate, str]],
    closest: tuple[OfferCandidate, OfferFit] | None,
    stale: Sequence[OfferCandidate] = (),
) -> tuple[str, OfferCandidate | None, tuple[DemandFit, ...]]:
    """Explain why the requirement has no fitting Z0 offer.

    ``stale`` carries offers that classify Z0 but whose official evidence is no
    longer known to be current. They are reported *before* the generic
    "nothing declares this category" message, because the honest explanation is
    that a free offer exists and its support expired -- not that none was found.
    The offer itself is returned as the closest candidate so the user still sees
    it; the refusal is to *guarantee* $0 on it, not to hide it.
    """

    if eligible_count == 0 and stale:
        candidate = sorted(stale, key=OfferCandidate.sort_key)[0]
        detail = candidate.evidence_currency.reason() or (
            "Its official evidence is no longer known to be current."
        )
        return (
            f"'{candidate.service_name}' ({candidate.provider_name}) classifies as "
            f"{Z0_TRUE_FREE} but cannot back a guaranteed-$0 architecture: {detail} "
            "Re-verify it against the provider's official pricing page before relying on it.",
            candidate,
            (),
        )
    if eligible_count == 0:
        if rejected:
            reasons = "; ".join(sorted({reason for _, reason in rejected}))
            return (
                f"No published Z0 offer in category '{requirement.category}' meets the "
                f"constraints ({reasons}).",
                None,
                (),
            )
        return (
            f"No published Z0 offer declares category '{requirement.category}'.",
            None,
            (),
        )
    if closest is not None:
        candidate, fit = closest
        blocking = fit.blocking
        detail = "; ".join(df.reason for df in blocking)
        return (
            f"The closest Z0 offer '{candidate.service_name}' ({candidate.provider_name}) "
            f"cannot cover: {detail}",
            candidate,
            blocking,
        )
    return (
        f"No Z0 offer in category '{requirement.category}' can cover the demands.",
        None,
        (),
    )


# --------------------------------------------------------------------------- #
# Orchestrator                                                                #
# --------------------------------------------------------------------------- #


def _not_free_for(pool: CandidatePool, requirement: Requirement, index: int) -> list[NotFreeOption]:
    options: list[NotFreeOption] = []
    matches = [c for c in pool.not_free if c.category_slug == requirement.category]
    for candidate in sorted(matches, key=OfferCandidate.sort_key):
        fit = evaluate_offer(requirement.demands, candidate.quotas)
        options.append(
            NotFreeOption(
                requirement_index=index,
                category=requirement.category,
                candidate=candidate,
                offer_fit=fit,
            )
        )
    return options


def recommend(request: RecommendationRequest, pool: CandidatePool) -> RecommendationResult:
    """Produce the deterministic recommendation for ``request`` over ``pool``."""

    components: list[ComponentRecommendation] = []
    impossible: list[ImpossibleResolution] = []
    not_free: list[NotFreeOption] = []

    for index, requirement in enumerate(request.requirements):
        eligible, rejected = _eligible(pool.z0, requirement)
        evaluated = _evaluate(eligible, requirement.demands)
        fitting = [(c, f) for c, f in evaluated if f.fits]

        # Z1/Z2 options for this category always go to the separate section.
        not_free.extend(_not_free_for(pool, requirement, index))

        if fitting:
            best_candidate, best_fit = min(fitting, key=_order_key)
            components.append(
                ComponentRecommendation(
                    requirement_index=index,
                    category=requirement.category,
                    label=requirement.label,
                    candidate=best_candidate,
                    offer_fit=best_fit,
                )
            )
            continue

        # Blocking requirement -> the strict impossible order.
        non_fitting = [(c, f) for c, f in evaluated if not f.fits]
        closest = _closest(non_fitting)
        reason, closest_candidate, blocking_fits = _blocking_reason(
            requirement,
            len(eligible),
            rejected,
            closest,
            [c for c in pool.stale if c.category_slug == requirement.category],
        )

        reductions: tuple[ReductionStep, ...] = ()
        recalculated: ComponentRecommendation | None = None
        if closest is not None:
            closest_candidate = closest[0]
            reductions, reduced_demands, feasible = _reduce(requirement, closest[0])
            if feasible:
                recalc_eval = _evaluate(eligible, reduced_demands)
                recalc_fitting = [(c, f) for c, f in recalc_eval if f.fits]
                if recalc_fitting:
                    rc_candidate, rc_fit = min(recalc_fitting, key=_order_key)
                    recalculated = ComponentRecommendation(
                        requirement_index=index,
                        category=requirement.category,
                        label=requirement.label,
                        candidate=rc_candidate,
                        offer_fit=rc_fit,
                        reduced=True,
                        reduced_demands=reduced_demands,
                    )

        impossible.append(
            ImpossibleResolution(
                requirement_index=index,
                category=requirement.category,
                label=requirement.label,
                blocking_reason=reason,
                closest_candidate=closest_candidate,
                blocking_fits=blocking_fits,
                reductions=reductions,
                recalculated=recalculated,
                self_hosting=_self_hosting(pool, requirement),
            )
        )

    return RecommendationResult(
        workload_name=request.workload_name,
        priorities=FIXED_PRIORITIES,
        fully_zero_cost=not impossible,
        components=tuple(components),
        impossible=tuple(impossible),
        not_free=tuple(not_free),
    )


__all__: Sequence[str] = (
    "HOSTING_CATEGORIES",
    "ComponentRecommendation",
    "ReductionStep",
    "SelfHostingOption",
    "ImpossibleResolution",
    "NotFreeOption",
    "RecommendationResult",
    "recommend",
)
