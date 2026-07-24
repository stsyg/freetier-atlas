"""Templated, deterministic, evidence-backed explanation assembly (F006 s3).

Every string the adviser shows a user is assembled here from *persisted facts*
only -- the classify engine's reasons (derived from persisted material facts),
the exact quota math, the deterministic portability assessment, and the linked
:class:`~app.models.domain.Evidence`. Nothing is generated or guessed, and there
is no LLM: identical inputs produce identical text.

The two headline artefacts are:

* a per-component explanation (quota-math coverage + Z0-safety reasons +
  portability / lock-in / exit-plan + evidence references), and
* the whole-architecture **$0 proof** -- an explicit, checkable statement that
  every component is Z0 (via the engine) and every demand is covered with
  non-negative exact-Decimal headroom, so the architecture as a whole is $0.
"""

from __future__ import annotations

from collections.abc import Sequence

from .quota_math import OfferFit
from .recommend import (
    ComponentRecommendation,
    ImpossibleResolution,
    RecommendationResult,
)
from .select import OfferCandidate


def quota_math_lines(fit: OfferFit) -> list[str]:
    """Human-readable, exact-Decimal coverage lines for each demand."""

    lines: list[str] = []
    for df in fit.demand_fits:
        prefix = "covered" if df.covered else "NOT covered"
        boundary = " (exact boundary: zero headroom)" if df.is_boundary else ""
        lines.append(f"{df.metric}: {prefix}{boundary} -- {df.reason}")
    return lines


def z0_safety_lines(candidate: OfferCandidate) -> list[str]:
    """The classify engine's Z0-safety reasons for an offer (evidence-backed)."""

    lines = [
        f"Zero-cost class {candidate.zero_cost_class} confirmed by the classification "
        f"engine (persisted class '{candidate.persisted_class}' agrees).",
    ]
    lines.extend(candidate.reasons)
    return lines


def portability_lines(candidate: OfferCandidate) -> list[str]:
    """Portability / lock-in / exit-plan lines for an offer."""

    p = candidate.portability
    lines = [
        f"Portability: {p.label} (score {p.score}); lock-in: {p.lock_in_label}.",
    ]
    lines.extend(p.basis)
    lines.append("Exit plan:")
    lines.extend(f"  - {step}" for step in p.exit_plan)
    return lines


def evidence_refs(candidate: OfferCandidate) -> list[dict[str, object]]:
    """Structured references to the persisted evidence backing an offer.

    Uses only persisted evidence fields (title / url / official flag); these are
    catalogue provenance, never caller input.
    """

    refs: list[dict[str, object]] = []
    for row in candidate.evidence:
        refs.append(
            {
                "title": getattr(row, "title", None),
                "url": getattr(row, "url", None),
                "official": bool(getattr(row, "official", False)),
            }
        )
    return refs


def component_summary(component: ComponentRecommendation) -> str:
    """A one-line summary of a chosen component."""

    candidate = component.candidate
    scope = " (under reduced demand)" if component.reduced else ""
    return (
        f"{component.category}: {candidate.service_name} by {candidate.provider_name}"
        f"{scope} -- Z0 true-free, all demands covered with headroom."
    )


def zero_cost_proof(result: RecommendationResult) -> list[str]:
    """The whole-architecture $0 proof.

    Only meaningful when every requirement is satisfied by a Z0 offer; otherwise
    returns a single honest statement that the workload as requested cannot be
    guaranteed $0 (see the impossible-order resolutions).
    """

    if not result.fully_zero_cost:
        return [
            "The workload as requested cannot be guaranteed $0: one or more "
            "requirements have no fitting Z0 offer. See the ordered resolution "
            "(blocking reason -> reduction -> recalculation -> self-hosting) below.",
        ]

    proof: list[str] = [
        "$0 proof: every component below is classified Z0_TRUE_FREE by the "
        "classification engine (no card, no paid dependencies, every quota stops "
        "safely on exhaustion), and every demand is covered with non-negative, "
        "exact-Decimal headroom at a known unit.",
    ]
    for component in result.components:
        candidate = component.candidate
        proof.append(
            f"- {candidate.service_name} ({candidate.provider_name}) for "
            f"'{component.category}': {candidate.zero_cost_class}; "
            f"{len(component.offer_fit.demand_fits)} demand(s) covered."
        )
    proof.append(
        "Because every component is independently $0 and no component introduces a "
        "billing dependency, the whole architecture remains $0."
    )
    return proof


def impossible_lines(resolution: ImpossibleResolution) -> list[str]:
    """The ordered, human-readable impossible-workload resolution.

    Emits the four steps strictly in product order: (1) the blocking reason, then
    (2) reduction, (3) recalculation, and (4) self-hosting.
    """

    lines: list[str] = [f"1. Blocking: {resolution.blocking_reason}"]

    if resolution.reductions:
        lines.append("2. Reduction:")
        for step in resolution.reductions:
            if step.feasible and step.reduced_amount is not None:
                lines.append(
                    f"   - {step.metric}: reduce {step.original_amount} -> "
                    f"{step.reduced_amount} {step.original_unit}. {step.reason}"
                )
            else:
                lines.append(f"   - {step.metric}: cannot reduce. {step.reason}")
    else:
        lines.append("2. Reduction: not applicable (no comparable Z0 offer to reduce toward).")

    if resolution.recalculated is not None:
        rc = resolution.recalculated
        lines.append(
            "3. Recalculation: under the reduced demand, "
            f"{rc.candidate.service_name} ({rc.candidate.provider_name}) fits and stays $0."
        )
    else:
        lines.append(
            "3. Recalculation: even after reduction, no Z0 offer covers the demand "
            "(fail closed -- not guaranteed $0)."
        )

    if resolution.self_hosting:
        lines.append("4. Self-hosting:")
        lines.extend(f"   - {opt.note}" for opt in resolution.self_hosting)
    else:
        lines.append(
            "4. Self-hosting: no published self-hostable (Z3) building block is "
            "available in this category."
        )

    return lines


__all__: Sequence[str] = (
    "quota_math_lines",
    "z0_safety_lines",
    "portability_lines",
    "evidence_refs",
    "component_summary",
    "zero_cost_proof",
    "impossible_lines",
)
