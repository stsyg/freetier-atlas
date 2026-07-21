"""The Z0 classification engine.

A pure, deterministic mapping from an offer's *material facts* to an explainable
zero-cost class. The engine performs no I/O, no network access, and no
inference beyond the supplied facts: when a material condition is unknown or
contradictory it returns ``UNKNOWN`` rather than guessing. This is the product's
central anti-false-claim safeguard -- **no unknown material condition may ever
yield Z0** (docs/PRODUCT_REQUIREMENTS.md "Zero-cost classes").

The decision gates, in precedence order, are:

1. **Z3 (self-hosted building block).** ``self_hosted_open_source`` offers are
   free software that require self-provided infrastructure. They only join a Z0
   architecture when hosted on verified Z0 infrastructure (composition is
   deferred to the hosting task), so the offer itself is classified Z3.
2. **Z1 (billing exposure).** A required payment card, paid dependencies, or a
   quota whose exhaustion triggers ``automatic_billing`` is a definite billing
   exposure and can never be Z0.
3. **UNKNOWN.** Any unknown material condition (card requirement, paid-dependency
   status, an ``unknown`` or unrecognised exhaustion behaviour, or the total
   absence of quota data) blocks Z0. Per the product safety rule an unknown
   material condition yields ``UNKNOWN`` rather than being guessed into a more
   specific class, so this gate precedes the Z2 temporary/conditional gate.
4. **Z2 (temporary or conditional).** Trials, new-customer credits, time-bounded
   availability windows, eligibility-gated programs, or a quota that requires a
   manual paid upgrade to continue are temporary/conditional, not true $0.
   Reached only when every material condition is known.
5. **Z0 (true $0).** Only when every billing gate is explicitly clear *and*
   every quota exhaustion behaviour is a safe stop-type.

The closed vocabularies are imported from :mod:`app.models.vocab` so the engine
and the persisted schema share a single source of truth.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from app.models.vocab import EXHAUSTION_BEHAVIOURS, OFFER_TYPES

# --- Zero-cost class labels (must match app.models.vocab.ZERO_COST_CLASSES) ---
Z0_TRUE_FREE = "Z0_TRUE_FREE"
Z1_BILLING_EXPOSURE = "Z1_BILLING_EXPOSURE"
Z2_TEMPORARY_OR_CONDITIONAL = "Z2_TEMPORARY_OR_CONDITIONAL"
Z3_SELF_HOSTED_BUILDING_BLOCK = "Z3_SELF_HOSTED_BUILDING_BLOCK"
UNKNOWN = "UNKNOWN"

# --- Exhaustion-behaviour partitions (their union must equal the vocabulary) ---
# A quota that triggers automatic billing on exhaustion is a billing exposure.
BILLING_EXHAUSTION: frozenset[str] = frozenset({"automatic_billing"})
# A quota whose exhaustion behaviour is not known blocks a Z0 verdict.
UNKNOWN_EXHAUSTION: frozenset[str] = frozenset({"unknown"})
# Continuation requires a manual (paid) upgrade -- conditional, not true $0.
CONDITIONAL_EXHAUSTION: frozenset[str] = frozenset({"manual_upgrade_required"})
# Safe stop-types: usage halts/degrades but no charge is incurred (Z0-eligible).
SAFE_EXHAUSTION: frozenset[str] = frozenset(
    {
        "hard_stop",
        "request_rejected",
        "throttled",
        "service_sleeps",
        "read_only",
        "deployment_blocked",
        "site_disabled_until_reset",
        "resource_reclaimed",
        "data_deleted",
    }
)

# --- Offer-type partitions relevant to classification ---
SELF_HOSTED_OFFER_TYPES: frozenset[str] = frozenset({"self_hosted_open_source"})
# Offer types that are inherently temporary or conditional.
TEMPORARY_CONDITIONAL_OFFER_TYPES: frozenset[str] = frozenset(
    {
        "trial",
        "new_customer_credit",
        "startup_program",
        "student_program",
        "open_source_program",
        "hackathon_promotion",
    }
)


@dataclass(frozen=True)
class OfferFacts:
    """The minimal material facts the engine classifies.

    ``requires_card`` and ``has_paid_dependencies`` are tri-state: ``True`` /
    ``False`` are known, ``None`` means *unknown* (which blocks Z0).
    ``exhaustion_behaviours`` is the collection of every quota's exhaustion
    behaviour for the offer version under consideration; an empty collection
    means there is no quota data (which also blocks Z0).
    """

    offer_type: str
    requires_card: bool | None = None
    has_paid_dependencies: bool | None = None
    exhaustion_behaviours: tuple[str, ...] = ()
    eligibility: str | None = None
    available_from: date | None = None
    available_until: date | None = None

    def __post_init__(self) -> None:
        # Normalise the exhaustion behaviours to a deterministic tuple.
        object.__setattr__(self, "exhaustion_behaviours", tuple(self.exhaustion_behaviours))


@dataclass(frozen=True)
class ClassificationResult:
    """The outcome of a classification: a class plus its human-readable rationale."""

    zero_cost_class: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    blocking_conditions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_zero_cost(self) -> bool:
        """True only for a Z0_TRUE_FREE verdict."""

        return self.zero_cost_class == Z0_TRUE_FREE


def _availability_reasons(facts: OfferFacts, as_of: date) -> list[str]:
    """Return temporary/conditional reasons derived from the availability window."""

    reasons: list[str] = []
    if facts.available_until is not None:
        if facts.available_until < as_of:
            reasons.append(f"Offer availability ended on {facts.available_until.isoformat()}.")
        else:
            reasons.append(
                "Offer has a bounded availability window ending "
                f"{facts.available_until.isoformat()}."
            )
    return reasons


def classify(facts: OfferFacts, *, as_of: date | None = None) -> ClassificationResult:
    """Classify ``facts`` into a zero-cost class with an explanation.

    Deterministic and side-effect-free: identical inputs always produce an
    identical :class:`ClassificationResult`.
    """

    as_of = as_of or date.today()
    behaviours = facts.exhaustion_behaviours

    # Gate 1: self-hosted building block -- determined by nature, before billing.
    if facts.offer_type in SELF_HOSTED_OFFER_TYPES:
        return ClassificationResult(
            zero_cost_class=Z3_SELF_HOSTED_BUILDING_BLOCK,
            reasons=(
                f"Offer type '{facts.offer_type}' is free software that requires "
                "you to provide the hosting infrastructure.",
            ),
            blocking_conditions=(
                "Requires self-provided infrastructure; only counts toward a Z0 "
                "architecture when run on verified Z0 infrastructure.",
            ),
        )

    # Gate 2: definite billing exposure -> Z1 (dominates everything below).
    billing: list[str] = []
    if facts.requires_card is True:
        billing.append("A payment card is required.")
    if facts.has_paid_dependencies is True:
        billing.append("The offer has paid dependencies.")
    if any(b in BILLING_EXHAUSTION for b in behaviours):
        billing.append("A quota triggers automatic billing when exhausted.")
    if billing:
        return ClassificationResult(
            zero_cost_class=Z1_BILLING_EXPOSURE,
            reasons=tuple(billing),
            blocking_conditions=tuple(billing),
        )

    # Gate 3: any unknown material condition blocks Z0 -> UNKNOWN.
    # Per the safety rule this precedes the Z2 gate: an unknown condition must
    # yield UNKNOWN rather than being guessed into a temporary/conditional class.
    unknown: list[str] = []
    if facts.requires_card is None:
        unknown.append("Whether a payment card is required is unknown.")
    if facts.has_paid_dependencies is None:
        unknown.append("Whether the offer has paid dependencies is unknown.")
    if not behaviours:
        unknown.append("No quota data is available to confirm a safe exhaustion behaviour.")
    if any(b in UNKNOWN_EXHAUSTION for b in behaviours):
        unknown.append("A quota's exhaustion behaviour is unknown.")
    # Any exhaustion behaviour outside the known partitions is treated as unknown.
    known = SAFE_EXHAUSTION | BILLING_EXHAUSTION | UNKNOWN_EXHAUSTION | CONDITIONAL_EXHAUSTION
    unrecognised = sorted({b for b in behaviours if b not in known})
    unknown.extend(
        f"Unrecognised exhaustion behaviour '{b}' cannot be confirmed safe." for b in unrecognised
    )
    if unknown:
        return ClassificationResult(
            zero_cost_class=UNKNOWN,
            reasons=tuple(unknown),
            blocking_conditions=tuple(unknown),
        )

    # Gate 4: temporary or conditional -> Z2 (every material condition is known).
    conditional: list[str] = []
    if facts.offer_type in TEMPORARY_CONDITIONAL_OFFER_TYPES:
        conditional.append(
            f"Offer type '{facts.offer_type}' is temporary or eligibility-conditional."
        )
    conditional.extend(_availability_reasons(facts, as_of))
    if any(b in CONDITIONAL_EXHAUSTION for b in behaviours):
        conditional.append("A quota requires a manual paid upgrade to continue after exhaustion.")
    if conditional:
        return ClassificationResult(
            zero_cost_class=Z2_TEMPORARY_OR_CONDITIONAL,
            reasons=tuple(conditional),
            blocking_conditions=tuple(conditional),
        )

    # Gate 5: everything explicitly clear and every quota stops safely -> Z0.
    reasons = (
        "No payment card is required.",
        "The offer has no paid dependencies.",
        "Every quota exhaustion behaviour is a safe stop "
        "(usage halts or degrades without incurring a charge).",
        "Usage remains $0: classified Z0_TRUE_FREE.",
    )
    return ClassificationResult(zero_cost_class=Z0_TRUE_FREE, reasons=reasons)


def known_zero_cost_classes() -> tuple[str, ...]:
    """The five zero-cost class labels this engine can emit, in canonical order."""

    return (
        Z0_TRUE_FREE,
        Z1_BILLING_EXPOSURE,
        Z2_TEMPORARY_OR_CONDITIONAL,
        Z3_SELF_HOSTED_BUILDING_BLOCK,
        UNKNOWN,
    )


def _partition_covers_vocabulary() -> bool:
    """True if the four exhaustion partitions exactly tile the vocabulary.

    Exposed for tests: guards against silent drift if the exhaustion vocabulary
    in :mod:`app.models.vocab` changes without updating the engine.
    """

    partitioned = SAFE_EXHAUSTION | BILLING_EXHAUSTION | UNKNOWN_EXHAUSTION | CONDITIONAL_EXHAUSTION
    return partitioned == set(EXHAUSTION_BEHAVIOURS)


def _offer_types_recognised() -> bool:
    """True if every classification-relevant offer type is in the vocabulary."""

    referenced = SELF_HOSTED_OFFER_TYPES | TEMPORARY_CONDITIONAL_OFFER_TYPES
    return referenced <= set(OFFER_TYPES)


def summarise(results: Iterable[ClassificationResult]) -> dict[str, int]:
    """Count results by class label (convenience for batch reporting/tests)."""

    counts: dict[str, int] = {label: 0 for label in known_zero_cost_classes()}
    for result in results:
        counts[result.zero_cost_class] = counts.get(result.zero_cost_class, 0) + 1
    return counts


__all__: Sequence[str] = (
    "OfferFacts",
    "ClassificationResult",
    "classify",
    "known_zero_cost_classes",
    "summarise",
    "Z0_TRUE_FREE",
    "Z1_BILLING_EXPOSURE",
    "Z2_TEMPORARY_OR_CONDITIONAL",
    "Z3_SELF_HOSTED_BUILDING_BLOCK",
    "UNKNOWN",
    "SAFE_EXHAUSTION",
    "BILLING_EXHAUSTION",
    "UNKNOWN_EXHAUSTION",
    "CONDITIONAL_EXHAUSTION",
    "SELF_HOSTED_OFFER_TYPES",
    "TEMPORARY_CONDITIONAL_OFFER_TYPES",
)
