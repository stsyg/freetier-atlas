"""Deterministic portability / lock-in / exit-plan assessment (F006 slice 3).

The product's second and third adviser priorities (after an exact $0 guarantee)
are **portability** and **low lock-in** (docs/PRODUCT_REQUIREMENTS.md, D014).
This module derives all three -- a portability score + label, a lock-in label,
and a concrete exit plan -- purely and deterministically from persisted facts:
a service's ``deployment_model`` (``managed`` / ``self_hosted``) and its
``portability_traits`` (a JSONB tag list). Identical inputs always yield an
identical assessment.

The scoring is intentionally conservative and honest:

* a base score comes from the deployment model (a self-hosted building block you
  run yourself is inherently more portable than a managed service),
* each *recognised* portability trait nudges the score up, each recognised
  lock-in trait nudges it down, and
* an **unrecognised trait is neutral** -- it is recorded and echoed in the basis
  but never guessed into a score change ("unknown is better than guessed").

All arithmetic uses :class:`~decimal.Decimal` (quantised to two places) so no
binary-float artefact can make an otherwise-identical input score differently.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_TWO_PLACES = Decimal("0.01")

# --- Base scores by deployment model ---------------------------------------- #
_BASE_SELF_HOSTED = Decimal("0.70")
_BASE_MANAGED = Decimal("0.40")
_BASE_UNKNOWN = Decimal("0.30")

_TRAIT_STEP = Decimal("0.10")

#: Recognised portability-*enhancing* traits (normalised: lowercase, separators
#: collapsed to ``_``). Membership is exact; anything else is treated as neutral.
_POSITIVE_TRAITS: frozenset[str] = frozenset(
    {
        "open_source",
        "open_standards",
        "open_formats",
        "standard_api",
        "standard_protocol",
        "data_export",
        "exportable_data",
        "portable_data",
        "self_hostable",
        "container_based",
        "oci_images",
        "s3_compatible",
        "postgres_compatible",
        "no_egress_lock_in",
    }
)

#: Recognised lock-in traits that reduce portability.
_NEGATIVE_TRAITS: frozenset[str] = frozenset(
    {
        "proprietary_api",
        "proprietary_format",
        "proprietary_runtime",
        "vendor_lock_in",
        "no_data_export",
        "closed_source",
        "egress_fees",
    }
)

# --- Label thresholds ------------------------------------------------------- #
_HIGH_THRESHOLD = Decimal("0.66")
_MEDIUM_THRESHOLD = Decimal("0.33")


def normalize_trait(raw: str) -> str:
    """Normalise a trait tag: lowercase, trim, collapse ``-``/space to ``_``."""

    cleaned = raw.strip().lower()
    for sep in ("-", " "):
        cleaned = cleaned.replace(sep, "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


@dataclass(frozen=True)
class PortabilityAssessment:
    """A deterministic portability / lock-in / exit-plan result."""

    score: Decimal
    label: str
    lock_in_label: str
    deployment_model: str
    positive_traits: tuple[str, ...]
    negative_traits: tuple[str, ...]
    unknown_traits: tuple[str, ...]
    basis: tuple[str, ...]
    exit_plan: tuple[str, ...]


def _clamp(value: Decimal) -> Decimal:
    if value < Decimal(0):
        value = Decimal(0)
    if value > Decimal(1):
        value = Decimal(1)
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _label(score: Decimal) -> str:
    if score >= _HIGH_THRESHOLD:
        return "high"
    if score >= _MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _lock_in_label(portability_label: str) -> str:
    return {"high": "low", "medium": "moderate", "low": "high"}[portability_label]


def _classify_traits(
    traits: Sequence[object],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    positive: list[str] = []
    negative: list[str] = []
    unknown: list[str] = []
    for raw in traits:
        if not isinstance(raw, str):
            continue
        key = normalize_trait(raw)
        if not key:
            continue
        if key in _POSITIVE_TRAITS:
            positive.append(key)
        elif key in _NEGATIVE_TRAITS:
            negative.append(key)
        else:
            unknown.append(key)
    # Deterministic, de-duplicated ordering.
    return (
        tuple(sorted(set(positive))),
        tuple(sorted(set(negative))),
        tuple(sorted(set(unknown))),
    )


def _exit_plan(
    deployment_model: str,
    positive: tuple[str, ...],
    negative: tuple[str, ...],
) -> tuple[str, ...]:
    lines: list[str] = []
    if deployment_model == "self_hosted":
        lines.append(
            "Self-hosted building block: you own the deployment, so you can move it "
            "to any other host (including another Z0 host) without provider approval."
        )
    elif deployment_model == "managed":
        lines.append(
            "Managed service: plan an exit by exporting your data and redeploying on "
            "an alternative provider; verify the export format before you depend on it."
        )
    else:
        lines.append(
            "Deployment model is unknown: treat lock-in as high and confirm an export "
            "path before committing critical data."
        )

    if "data_export" in positive or "exportable_data" in positive or "portable_data" in positive:
        lines.append("Data export is available, easing migration off this component.")
    if "s3_compatible" in positive:
        lines.append("S3-compatible: another S3-compatible store can be a drop-in target.")
    if "postgres_compatible" in positive:
        lines.append("PostgreSQL-compatible: a standard pg_dump/restore is a viable exit.")
    if "open_source" in positive or "self_hostable" in positive:
        lines.append("Open-source / self-hostable: you can run it yourself if the offer changes.")

    if "no_data_export" in negative:
        lines.append("No data export is documented: migration may require manual re-entry.")
    if "proprietary_format" in negative or "proprietary_api" in negative:
        lines.append("Proprietary API/format: budget for adapter work when migrating.")
    if "egress_fees" in negative:
        lines.append("Egress fees may apply on exit: factor bulk-download cost into the plan.")

    # Always retain a deterministic fallback so an exit plan is never empty.
    lines.append(
        "Retain a periodic backup of your data and configuration so you can rebuild "
        "the component elsewhere at any time."
    )
    return tuple(lines)


def assess_portability(
    deployment_model: str | None,
    portability_traits: Sequence[object] | None,
) -> PortabilityAssessment:
    """Assess portability / lock-in / exit-plan from persisted service facts.

    ``deployment_model`` is ``"managed"`` or ``"self_hosted"`` (anything else is
    treated conservatively as unknown). ``portability_traits`` is the service's
    JSONB tag list. The result is fully deterministic and clamped to ``[0, 1]``.
    """

    model = (deployment_model or "").strip().lower()
    traits = list(portability_traits or [])
    positive, negative, unknown = _classify_traits(traits)

    if model == "self_hosted":
        base = _BASE_SELF_HOSTED
    elif model == "managed":
        base = _BASE_MANAGED
    else:
        base = _BASE_UNKNOWN
        model = "unknown"

    score = base + _TRAIT_STEP * (Decimal(len(positive)) - Decimal(len(negative)))
    score = _clamp(score)
    label = _label(score)

    basis: list[str] = [
        f"Base portability {base} for deployment model '{model}'.",
    ]
    if positive:
        basis.append(f"+{_TRAIT_STEP} each for portability traits: {', '.join(positive)}.")
    if negative:
        basis.append(f"-{_TRAIT_STEP} each for lock-in traits: {', '.join(negative)}.")
    if unknown:
        basis.append(
            "Unrecognised traits recorded but not scored (unknown is better than guessed): "
            + ", ".join(unknown)
            + "."
        )
    basis.append(f"Final portability score {score} -> '{label}' portability.")

    return PortabilityAssessment(
        score=score,
        label=label,
        lock_in_label=_lock_in_label(label),
        deployment_model=model,
        positive_traits=positive,
        negative_traits=negative,
        unknown_traits=unknown,
        basis=tuple(basis),
        exit_plan=_exit_plan(model, positive, negative),
    )


__all__: Sequence[str] = (
    "PortabilityAssessment",
    "assess_portability",
    "normalize_trait",
)
