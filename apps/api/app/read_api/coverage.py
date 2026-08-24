"""Pure, on-demand derivation of a provider x category coverage state (F008 S2).

This module answers one question: *given what the published catalogue actually
contains for a (provider, category) pair, what state does the evidence support?*
It is the counterpart to the human **declaration** stored in
``provider_category_coverage``.

Q11 -- derivation is computed, never stored
-------------------------------------------
Nothing here writes. The derived state is recomputed on every read and is
deliberately **not** persisted: a stored projection would become a second source
of truth that can silently go stale, which is exactly the failure mode the
``stale`` state exists to expose. The durable artefact for a declared-vs-derived
contradiction is an ordinary pending ``review_item`` raised by
:mod:`app.ingest.reconcile_coverage` -- the write side lives in ``ingest`` so the
read API keeps its "reads and never writes" invariant.

``not_offered`` is never derived
--------------------------------
:func:`derive_coverage_state` can return any state except ``not_offered``. An
empty result means "we have not verified this" (``unknown``), never "the
provider does not offer it". Asserting absence is a claim, and a claim needs a
human rationale, so ``not_offered`` can only arrive as a declaration. The
previous read-API behaviour -- ``published == 0 -> "not_offered"`` -- was exactly
the guess this rule forbids.

Precedence
----------
``conflicting`` > ``stale`` > ``verified_free`` > ``incomplete`` >
``offered_no_z0`` > ``unknown``.

Honesty first: an unresolved evidence contradiction or evidence past its refresh
window must not be masked by an optimistic free claim. ``offered_no_z0`` is
itself an assertion ("this category exists here but nothing in it is Z0"), so a
published offer we could not classify degrades to ``incomplete`` rather than
being counted as evidence for that assertion.

Materiality and display are separate decisions
----------------------------------------------
Deriving a state, deciding whether the derivation *contradicts* the declaration
(:data:`MATERIAL_MISMATCHES`), and deciding what the catalogue *presents*
(:data:`MATERIAL_MISMATCH_DISPLAY`) are three separate steps on purpose. Until
the ``f008-obsB`` ruling the second and third were fused: raising a
contradiction forced the display to ``conflicting``, so a rule that was right
about materiality could only be adopted by also changing what the public
catalogue claimed. Splitting them is what lets a stale free claim stop being
presented as free (correct) without a never-corroborated free claim being
suppressed for an ingest-coverage reason (incorrect).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from app.models.vocab import COVERAGE_STATES

#: The zero-cost class that makes an offer count as a verified free offer.
FREE_ZERO_COST_CLASS = "Z0_TRUE_FREE"

#: A published offer carrying this class has not been classified, so it supports
#: neither "there is a free offer" nor "there is no free offer".
UNCLASSIFIED_ZERO_COST_CLASS = "UNKNOWN"

#: The default when nothing is declared and nothing is known.
UNKNOWN = "unknown"

#: States :func:`derive_coverage_state` may return. ``not_offered`` is absent by
#: design -- see the module docstring. Tests assert this set exactly, so removing
#: the guard turns a test red.
DERIVABLE_STATES: frozenset[str] = frozenset(
    {"verified_free", "offered_no_z0", "incomplete", "stale", "conflicting", UNKNOWN}
)

#: (declared, derived) pairs that are a *material* contradiction and therefore
#: raise a pending review item.
#:
#: Three rules shape this set:
#:
#: * A derived ``unknown`` is material for **exactly one** declaration --
#:   ``verified_free``. Every other evidence-backed declaration may legitimately
#:   run ahead of the ingest pipeline (a provider slice declares
#:   ``offered_no_z0`` or ``not_offered`` with an official URL before, or
#:   without ever, publishing offers there), and flooding the review queue with
#:   those would train reviewers to ignore it. A *free claim* is different in
#:   kind: it is the one declaration that asserts the product's headline fact,
#:   so a catalogue that has never corroborated it is a question for a human.
#: * A declared hedge (``incomplete`` / ``stale`` / ``conflicting``) is already
#:   honest about uncertainty, so it never contradicts anything.
#: * Evidence past its refresh window cannot sustain a free claim. ``stale``
#:   means the snapshot the claim rests on has expired, and an expired claim is
#:   a guess -- "unknown is better than guessed" applies to the staleness path
#:   exactly as it applies to the evidence path.
#:
#: The Q9-A cases are the first four: declaring ``unknown`` or ``not_offered``
#: over a real published offer is the specific dishonesty this slice must catch.
MATERIAL_MISMATCHES: frozenset[tuple[str, str]] = frozenset(
    {
        # Silence over a real offer.
        (UNKNOWN, "verified_free"),
        (UNKNOWN, "offered_no_z0"),
        # Denial over a real offer, or over evidence that something is there.
        ("not_offered", "verified_free"),
        ("not_offered", "offered_no_z0"),
        ("not_offered", "incomplete"),
        ("not_offered", "stale"),
        ("not_offered", "conflicting"),
        # A free claim the catalogue does not support.
        ("verified_free", "offered_no_z0"),
        ("verified_free", "conflicting"),
        # A free claim whose backing evidence has expired (f008-obsB).
        ("verified_free", "stale"),
        # A free claim the catalogue has never corroborated (f008-obsC).
        ("verified_free", UNKNOWN),
        # A "no free tier here" claim the catalogue contradicts.
        ("offered_no_z0", "verified_free"),
        ("offered_no_z0", "conflicting"),
    }
)

#: How a material pair is *displayed*, where the generic ``conflicting`` collapse
#: would be wrong. Any material pair absent from this table displays
#: ``conflicting``.
#:
#: Raising a review item and choosing a display are two different decisions, and
#: conflating them is how a correct materiality rule produces an incorrect
#: public claim:
#:
#: * ``(verified_free, stale)`` displays ``stale``. The claim must stop being
#:   presented as free -- that is the whole point of the ruling -- but nothing
#:   here *disagrees*; the snapshot expired. Calling expiry a conflict would be
#:   its own small guess, and ``stale`` already means precisely this.
#: * ``(verified_free, unknown)`` keeps displaying ``verified_free``. A
#:   ``verified_free`` declaration is provenance-backed (the config loader
#:   rejects an offered category with no source), and an *absent* publication
#:   refutes nothing: the publication gate withholds offers by design, so most
#:   pairs derive ``unknown`` for reasons that have nothing to do with the
#:   claim's truth. Suppressing a true free claim is a defect too. The review
#:   item is the remedy here, not a downgrade.
MATERIAL_MISMATCH_DISPLAY: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        ("verified_free", "stale"): "stale",
        ("verified_free", UNKNOWN): "verified_free",
    }
)


@dataclass(frozen=True, slots=True)
class CoverageSignals:
    """What the published catalogue says about one (provider, category) pair.

    Deliberately plain data: gathering the signals is the caller's job, so the
    derivation itself is pure and exhaustively testable without a database.
    """

    #: Published offers in this category for this provider.
    published_offer_count: int = 0
    #: Published offers classified ``Z0_TRUE_FREE``.
    free_offer_count: int = 0
    #: Published offers we could not classify (``UNKNOWN`` zero-cost class).
    unclassified_offer_count: int = 0
    #: A pending, non-coverage evidence contradiction touches this pair.
    has_pending_contradiction: bool = False
    #: At least one source backing this pair is past its refresh window.
    has_stale_evidence: bool = False

    def __post_init__(self) -> None:
        for name in (
            "published_offer_count",
            "free_offer_count",
            "unclassified_offer_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.free_offer_count > self.published_offer_count:
            raise ValueError("free_offer_count cannot exceed published_offer_count")
        if self.unclassified_offer_count > self.published_offer_count:
            raise ValueError("unclassified_offer_count cannot exceed published_offer_count")


def derive_coverage_state(signals: CoverageSignals) -> str:
    """Derive the observed coverage state. Never returns ``not_offered``.

    See the module docstring for the precedence and its rationale.
    """

    if signals.has_pending_contradiction:
        return "conflicting"
    if signals.published_offer_count > 0 and signals.has_stale_evidence:
        return "stale"
    if signals.free_offer_count > 0:
        return "verified_free"
    if signals.published_offer_count == 0:
        # Nothing published: we have not verified this. Emphatically NOT
        # ``not_offered`` -- that guess is what F008 slice S2 removed.
        return UNKNOWN
    if signals.unclassified_offer_count > 0:
        return "incomplete"
    return "offered_no_z0"


def signals_from_offers(
    offers: Iterable[object],
    *,
    has_pending_contradiction: bool = False,
    has_stale_evidence: bool = False,
) -> CoverageSignals:
    """Tally already-filtered *published* offers into :class:`CoverageSignals`.

    ``offers`` need only expose ``zero_cost_class``; the caller is responsible
    for having filtered to published offers in the category of interest.
    """

    published = 0
    free = 0
    unclassified = 0
    for offer in offers:
        published += 1
        zero_cost_class = getattr(offer, "zero_cost_class", None)
        if zero_cost_class == FREE_ZERO_COST_CLASS:
            free += 1
        elif zero_cost_class in (None, UNCLASSIFIED_ZERO_COST_CLASS):
            unclassified += 1
    return CoverageSignals(
        published_offer_count=published,
        free_offer_count=free,
        unclassified_offer_count=unclassified,
        has_pending_contradiction=has_pending_contradiction,
        has_stale_evidence=has_stale_evidence,
    )


def is_material_mismatch(declared_state: str | None, derived_state: str) -> bool:
    """Does this declared/derived pair warrant human review?"""

    if declared_state is None:
        declared_state = UNKNOWN
    if declared_state == derived_state:
        return False
    return (declared_state, derived_state) in MATERIAL_MISMATCHES


def effective_state(declared_state: str | None, derived_state: str) -> str:
    """The state the catalogue should present for a pair.

    An undeclared pair is ``unknown``. A material contradiction surfaces as
    ``conflicting`` rather than silently preferring either side, unless
    :data:`MATERIAL_MISMATCH_DISPLAY` names a truer label for that specific
    pair. Otherwise the declaration stands. ``derived_state`` is always reported
    alongside, so nothing is hidden by this collapse.
    """

    if declared_state is None:
        return UNKNOWN
    if is_material_mismatch(declared_state, derived_state):
        return MATERIAL_MISMATCH_DISPLAY.get((declared_state, derived_state), "conflicting")
    return declared_state


def mismatch_detail(
    *,
    provider_slug: str,
    category_slug: str,
    declared_state: str | None,
    derived_state: str,
    signals: CoverageSignals,
) -> dict[str, object]:
    """Structured explanation of a mismatch, for a review item / test failure."""

    return {
        "provider": provider_slug,
        "category": category_slug,
        "declared_state": declared_state or UNKNOWN,
        "derived_state": derived_state,
        "published_offer_count": signals.published_offer_count,
        "free_offer_count": signals.free_offer_count,
        "unclassified_offer_count": signals.unclassified_offer_count,
        "has_pending_contradiction": signals.has_pending_contradiction,
        "has_stale_evidence": signals.has_stale_evidence,
    }


def describe_mismatch(detail: Mapping[str, object]) -> str:
    """Render :func:`mismatch_detail` as an actionable one-line message."""

    return (
        f"coverage declaration mismatch for {detail['provider']}/{detail['category']}: "
        f"declared {detail['declared_state']!r} but the published catalogue derives "
        f"{detail['derived_state']!r} "
        f"({detail['published_offer_count']} published, "
        f"{detail['free_offer_count']} zero-cost)"
    )


__all__: Sequence[str] = (
    "COVERAGE_STATES",
    "DERIVABLE_STATES",
    "FREE_ZERO_COST_CLASS",
    "MATERIAL_MISMATCHES",
    "MATERIAL_MISMATCH_DISPLAY",
    "UNKNOWN",
    "CoverageSignals",
    "derive_coverage_state",
    "describe_mismatch",
    "effective_state",
    "is_material_mismatch",
    "mismatch_detail",
    "signals_from_offers",
)
