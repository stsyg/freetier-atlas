"""Truth-table tests for the Z0 classification engine (offline, pure).

Covers every decision gate, every exhaustion behaviour (safe / billing /
conditional / unknown), boundary and contradictory inputs, the no-quota case,
the read-only ORM adapter, and the central safety invariant: no unknown
material condition may ever yield Z0.
"""

from __future__ import annotations

import itertools
from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest
from app.classify import (
    UNKNOWN,
    Z0_TRUE_FREE,
    Z1_BILLING_EXPOSURE,
    Z2_TEMPORARY_OR_CONDITIONAL,
    Z3_SELF_HOSTED_BUILDING_BLOCK,
    ClassificationResult,
    OfferFacts,
    classify,
    classify_offer,
    known_zero_cost_classes,
    summarise,
)
from app.classify.engine import (
    BILLING_EXHAUSTION,
    CONDITIONAL_EXHAUSTION,
    SAFE_EXHAUSTION,
    SELF_HOSTED_OFFER_TYPES,
    TEMPORARY_CONDITIONAL_OFFER_TYPES,
    UNKNOWN_EXHAUSTION,
    _offer_types_recognised,
    _partition_covers_vocabulary,
)
from app.models.domain import Offer, OfferVersion, Quota
from app.models.vocab import EXHAUSTION_BEHAVIOURS, OFFER_TYPES, ZERO_COST_CLASSES

_TODAY = date(2026, 1, 15)

#: The offer types this module DECLARES to be Z0-capable, written by hand as a
#: statement of intent. Reconciled against a measured sweep of the engine by
#: :func:`test_the_declared_offer_type_lists_match_the_measured_partition` -- a
#: typed list is a snapshot of the day it was typed and cannot notice drift on
#: its own.
DECLARED_Z0_CAPABLE: tuple[str, ...] = (
    "always_free",
    "recurring_quota",
    "personal_use_free",
    "other",
)

#: The offer types this module DECLARES to be inherently temporary/conditional.
#: Reconciled the same way.
DECLARED_TEMPORARY_CONDITIONAL: tuple[str, ...] = (
    "trial",
    "new_customer_credit",
    "startup_program",
    "student_program",
    "open_source_program",
    "hackathon_promotion",
)


class _CanonicalEqualityImpostor:
    def __init__(self) -> None:
        self.eq_calls = 0
        self.hash_calls = 0

    def __eq__(self, other: object) -> bool:
        self.eq_calls += 1
        return other == "always_free"

    def __hash__(self) -> int:
        self.hash_calls += 1
        return hash("always_free")


class _UnhashableEqualityImpostor:
    __hash__ = None  # type: ignore[assignment]

    def __init__(self) -> None:
        self.eq_calls = 0

    def __eq__(self, other: object) -> bool:
        self.eq_calls += 1
        return other == "always_free"


class _StringCoercionImpostor:
    def __init__(self) -> None:
        self.str_calls = 0

    def __str__(self) -> str:
        self.str_calls += 1
        return "always_free"


# --------------------------------------------------------------------------- #
# Vocabulary / single-source-of-truth guards
# --------------------------------------------------------------------------- #


def test_class_labels_match_vocabulary() -> None:
    assert known_zero_cost_classes() == ZERO_COST_CLASSES


def test_exhaustion_partitions_tile_the_vocabulary() -> None:
    # Every exhaustion behaviour belongs to exactly one partition, and together
    # the partitions exactly cover the closed vocabulary (drift guard).
    partitions = [SAFE_EXHAUSTION, BILLING_EXHAUSTION, UNKNOWN_EXHAUSTION, CONDITIONAL_EXHAUSTION]
    union: set[str] = set()
    for part in partitions:
        assert union.isdisjoint(part), "partitions must not overlap"
        union |= part
    assert union == set(EXHAUSTION_BEHAVIOURS)
    assert _partition_covers_vocabulary() is True


def test_referenced_offer_types_are_in_the_vocabulary() -> None:
    assert _offer_types_recognised() is True


# --------------------------------------------------------------------------- #
# Offer-type Z0 REACHABILITY -- measured across the whole input space
# --------------------------------------------------------------------------- #
#
# WHY THIS EXISTS. An Azure Level-2 evaluation recorded that ``offer_type=other``
# is Z0-CAPABLE in this engine -- absent from TEMPORARY_CONDITIONAL_OFFER_TYPES
# and gated nowhere else -- so ``docs/DATA_MODEL.md`` rule 3 ("route for review
# until the structure is evidenced") is an instruction to the AUTHOR and not a
# behaviour of the classifier. What withholds Z0 from the two rule-3 Azure
# offers is their unknown billing facts, never their offer type.
#
# That property was pinned for ``other`` alone, in the Azure adapter's test
# module, by naming two frozensets. Its NAME claimed "is not a safety
# mechanism" -- a statement about the whole engine -- while its REACH was two
# named constants in one module and one hard-coded offer type. A third gate on
# ``offer_type`` appearing anywhere would leave it green.
#
# So reachability is MEASURED here instead of asserted: every offer type in the
# closed vocabulary is classified against the engine's entire material input
# space, and the resulting Z0-reachable set is compared -- as a set -- against
# the set the engine's own declared gates imply. Behaviour disagreeing with the
# declared gates in EITHER direction is a failure, which is what makes "gated
# nowhere else" an executable claim rather than a remembered one.


def _z0_reachable(offer_type: str) -> bool:
    """True if ANY material-fact combination lets ``offer_type`` reach Z0.

    Exhaustive over every input the engine actually reads, rather than a single
    hand-picked "best case" probe: a probe proves one route is closed, a sweep
    proves every route is. ``eligibility`` is varied too even though the engine
    reads it nowhere today -- if a future gate starts consulting it, this sweep
    already covers it.

    ``available_from`` WAS in that category and no longer is: the opening gate
    consults it now. The sweep needed no edit when that gate landed, which is
    precisely the payoff the note above was written for. Its answers are
    unaffected because the sweep is EXISTENTIAL -- ``None`` and the past date
    still reach Z0 for any type that can -- so no type's reachability is decided
    by the not-yet-open combination alone.
    """

    tristate = (None, True, False)
    behaviours: tuple[tuple[str, ...], ...] = ((),) + tuple(
        (b,) for b in sorted(EXHAUSTION_BEHAVIOURS)
    )
    untils = (None, _TODAY - timedelta(days=1), _TODAY + timedelta(days=365))
    froms = (None, _TODAY - timedelta(days=1), _TODAY + timedelta(days=365))
    eligibilities = (None, "students")

    for card, deps, behaviour, until, since, eligible in itertools.product(
        tristate, tristate, behaviours, untils, froms, eligibilities
    ):
        result = classify(
            OfferFacts(
                offer_type=offer_type,
                requires_card=card,
                has_paid_dependencies=deps,
                exhaustion_behaviours=behaviour,
                eligibility=eligible,
                available_from=since,
                available_until=until,
            ),
            as_of=_TODAY,
        )
        if result.zero_cost_class == Z0_TRUE_FREE:
            return True
    return False


#: MEASURED at collection time from the engine itself, never typed out.
Z0_REACHABLE: frozenset[str] = frozenset(t for t in OFFER_TYPES if _z0_reachable(t))

#: What the engine's own declared gates imply the answer should be.
UNGATED_BY_DECLARATION: frozenset[str] = (
    frozenset(OFFER_TYPES) - SELF_HOSTED_OFFER_TYPES - TEMPORARY_CONDITIONAL_OFFER_TYPES
)


def test_the_reachability_sweep_is_not_vacuous() -> None:
    """A sweep that classified nothing would report every type unreachable.

    Two-sided by construction: the measured set must be neither empty nor the
    whole vocabulary, or the instrument is stuck rather than measuring.
    """

    assert len(OFFER_TYPES) >= 10, f"the offer-type vocabulary collapsed to {len(OFFER_TYPES)}"
    assert Z0_REACHABLE, "no offer type reached Z0 at all; the sweep is not classifying"
    assert Z0_REACHABLE != frozenset(OFFER_TYPES), (
        "EVERY offer type reached Z0, including self-hosted and trial types. "
        "The sweep is not discriminating -- it would pass no matter what the engine did."
    )


def test_z0_reachability_matches_the_engine_s_declared_gates() -> None:
    """Measured reachability == what SELF_HOSTED + TEMPORARY_CONDITIONAL imply.

    This is the "gated nowhere else" claim, executed. Set equality fails in both
    directions and names the offending type either way:

    * a type reachable but NOT ungated by declaration => a declared gate stopped
      working, and something the product means to withhold can now be published
      as free. This is the unsupported-free-claim direction and the reason the
      test exists.
    * a type ungated by declaration but NOT reachable => a gate on ``offer_type``
      exists somewhere OTHER than the two declared frozensets. That may well be
      an improvement, but it means the engine's documented model of itself is
      wrong, and every docstring asserting "gated nowhere else" is now false.

    ``unknown is better than guessed`` cuts both ways: a wrongly-WITHHELD free
    offer is also a defect, so this is not a one-directional safety assertion.
    """

    assert Z0_REACHABLE == UNGATED_BY_DECLARATION, (
        "Z0 reachability no longer matches the engine's declared offer-type gates.\n"
        f"  measured reachable:      {sorted(Z0_REACHABLE)}\n"
        f"  ungated by declaration:  {sorted(UNGATED_BY_DECLARATION)}\n"
        f"  reachable but gated:     {sorted(Z0_REACHABLE - UNGATED_BY_DECLARATION)}\n"
        f"  gated but unreachable:   {sorted(UNGATED_BY_DECLARATION - Z0_REACHABLE)}\n"
        "Either a declared gate stopped working (a withheld offer can now be published "
        "free), or offer_type is now gated somewhere outside SELF_HOSTED_OFFER_TYPES and "
        "TEMPORARY_CONDITIONAL_OFFER_TYPES. Find which before changing this test."
    )


def test_offer_type_other_is_z0_reachable_and_that_is_recorded_here() -> None:
    """DRIFT DETECTOR for ``other``. Read the message before "fixing" this.

    ``other`` is the vocabulary's escape hatch and ``docs/DATA_MODEL.md`` rule 3
    tells authors to reach for it when a structure is not yet evidenced. It
    carries NO safety weight: it reaches Z0 exactly as readily as
    ``always_free``. Nothing in the codebase said so until the Azure slice, and
    what did say so covered one value in one adapter's tests.

    If a later slice gates ``other``, this test goes RED **because the engine
    became SAFER**. That is not a regression. Update it deliberately, together
    with the docstrings in ``apps/api/app/ingest/adapters/profiles/azure.py``,
    ``docs/PROVIDER_ADAPTERS.md`` and ``docs/DATA_MODEL.md`` that state the
    opposite. Do not delete it and do not weaken it to green.
    """

    assert "other" in OFFER_TYPES, "the vocabulary no longer contains 'other'"
    assert "other" in Z0_REACHABLE, (
        "'other' can no longer reach Z0. The engine has become SAFER than when this "
        "test was written -- an IMPROVEMENT, not a regression. Record the new behaviour "
        "deliberately here and update the docs that claim rule-3 offers are withheld "
        "only by their unknown facts. Do not delete this test."
    )
    assert "other" not in TEMPORARY_CONDITIONAL_OFFER_TYPES
    assert "other" not in SELF_HOSTED_OFFER_TYPES


def test_every_temporary_conditional_type_is_refused_z0_on_every_route() -> None:
    """REFUSE arm, exhaustively: no input at all lets these reach Z0."""

    assert TEMPORARY_CONDITIONAL_OFFER_TYPES, "the temporary/conditional partition is empty"
    reachable = sorted(t for t in TEMPORARY_CONDITIONAL_OFFER_TYPES if t in Z0_REACHABLE)
    assert not reachable, (
        f"temporary/conditional offer types reached Z0: {reachable}. A trial or "
        "credit-backed offer can now be published as true $0."
    )
    assert SELF_HOSTED_OFFER_TYPES.isdisjoint(Z0_REACHABLE), (
        f"a self-hosted offer type reached Z0: {sorted(SELF_HOSTED_OFFER_TYPES & Z0_REACHABLE)}"
    )


def test_a_legitimately_free_offer_type_is_still_permitted() -> None:
    """PERMIT arm. A guard that cannot be shown to permit may have broken the product.

    ``always_free`` with every material condition explicitly clear is the
    canonical true-$0 offer. If the refusal assertions above ever pass because
    the engine stopped emitting Z0 at all, this fails and says so.
    """

    assert "always_free" in Z0_REACHABLE, "the canonical free offer type cannot reach Z0"
    result = classify(
        OfferFacts(
            offer_type="always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z0_TRUE_FREE
    assert result.blocking_conditions == ()


def test_the_declared_offer_type_lists_match_the_measured_partition() -> None:
    """The hand-written parametrize lists, reconciled against measurement.

    ``DECLARED_Z0_CAPABLE`` and ``DECLARED_TEMPORARY_CONDITIONAL`` drive the
    truth-table tests below. Both were typed by hand, and a typed list is a
    snapshot of the day it was written: adding a twelfth offer type to the
    vocabulary would leave it classified by NO test in this module while every
    existing test stayed green.

    Reconciling them here closes that. The lists remain as an explicit statement
    of intent; disagreement with the engine is the failure.
    """

    assert frozenset(DECLARED_Z0_CAPABLE) == Z0_REACHABLE, (
        "the hand-written Z0-capable list has drifted from measured reachability.\n"
        f"  declared: {sorted(DECLARED_Z0_CAPABLE)}\n"
        f"  measured: {sorted(Z0_REACHABLE)}"
    )
    assert frozenset(DECLARED_TEMPORARY_CONDITIONAL) == TEMPORARY_CONDITIONAL_OFFER_TYPES, (
        "the hand-written temporary/conditional list has drifted from the engine.\n"
        f"  declared: {sorted(DECLARED_TEMPORARY_CONDITIONAL)}\n"
        f"  engine:   {sorted(TEMPORARY_CONDITIONAL_OFFER_TYPES)}"
    )
    assert len(DECLARED_Z0_CAPABLE) == len(set(DECLARED_Z0_CAPABLE))
    assert len(DECLARED_TEMPORARY_CONDITIONAL) == len(set(DECLARED_TEMPORARY_CONDITIONAL))

    # Every offer type in the vocabulary lands in exactly one partition, so a
    # NEW one cannot be added without a test in this module classifying it.
    partitions = [
        frozenset(DECLARED_Z0_CAPABLE),
        frozenset(DECLARED_TEMPORARY_CONDITIONAL),
        SELF_HOSTED_OFFER_TYPES,
    ]
    union: set[str] = set()
    for part in partitions:
        assert union.isdisjoint(part), f"offer-type partitions overlap on {sorted(union & part)}"
        union |= part
    assert union == set(OFFER_TYPES), (
        "the offer-type partitions do not tile the vocabulary.\n"
        f"  unclassified by any partition: {sorted(set(OFFER_TYPES) - union)}\n"
        f"  in a partition but not in the vocabulary: {sorted(union - set(OFFER_TYPES))}"
    )


# --------------------------------------------------------------------------- #
# Z0 -- true $0
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("offer_type", DECLARED_Z0_CAPABLE)
@pytest.mark.parametrize("safe_behaviour", sorted(SAFE_EXHAUSTION))
def test_z0_for_cleared_gates_and_safe_exhaustion(offer_type: str, safe_behaviour: str) -> None:
    result = classify(
        OfferFacts(
            offer_type=offer_type,
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=(safe_behaviour,),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z0_TRUE_FREE
    assert result.is_zero_cost is True
    assert result.blocking_conditions == ()
    assert result.reasons  # non-empty explanation


def test_z0_with_multiple_safe_quotas() -> None:
    result = classify(
        OfferFacts(
            offer_type="always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop", "throttled", "read_only"),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z0_TRUE_FREE


# --------------------------------------------------------------------------- #
# Z1 -- billing exposure (dominates everything except Z3 self-hosted nature)
# --------------------------------------------------------------------------- #


def test_z1_when_card_required() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=True,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z1_BILLING_EXPOSURE
    assert any("card" in c.lower() for c in result.blocking_conditions)


def test_z1_when_paid_dependencies() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=True,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z1_BILLING_EXPOSURE
    assert any("paid dependencies" in c.lower() for c in result.blocking_conditions)


def test_z1_when_quota_triggers_automatic_billing() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("throttled", "automatic_billing"),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z1_BILLING_EXPOSURE
    assert any("automatic billing" in c.lower() for c in result.blocking_conditions)


def test_z1_dominates_contradictory_always_free_with_card() -> None:
    # Contradictory: "always_free" but a card is required -> billing gate wins.
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=True,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z1_BILLING_EXPOSURE


def test_z1_even_when_other_conditions_unknown() -> None:
    # A definite billing exposure is a known fact even if paid-deps is unknown.
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=True,
            has_paid_dependencies=None,
            exhaustion_behaviours=("unknown",),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z1_BILLING_EXPOSURE


# --------------------------------------------------------------------------- #
# Z2 -- temporary or conditional
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("offer_type", DECLARED_TEMPORARY_CONDITIONAL)
def test_z2_for_temporary_conditional_offer_types(offer_type: str) -> None:
    result = classify(
        OfferFacts(
            offer_type,
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z2_TEMPORARY_OR_CONDITIONAL
    assert result.blocking_conditions


def test_z2_for_bounded_future_availability_window() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
            available_until=_TODAY + timedelta(days=30),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z2_TEMPORARY_OR_CONDITIONAL
    assert any("bounded availability" in c.lower() for c in result.blocking_conditions)


def test_z2_for_expired_availability_window() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
            available_until=_TODAY - timedelta(days=1),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z2_TEMPORARY_OR_CONDITIONAL
    assert any("ended" in c.lower() for c in result.blocking_conditions)


def test_z2_for_manual_upgrade_required_quota() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("throttled", "manual_upgrade_required"),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z2_TEMPORARY_OR_CONDITIONAL
    assert any("manual paid upgrade" in c.lower() for c in result.blocking_conditions)


def test_unknown_dominates_trial_with_unknown_card() -> None:
    # A temporary/conditional offer type must NOT mask an unknown material
    # condition: the safety rule requires UNKNOWN, not Z2.
    result = classify(
        OfferFacts(
            "trial",
            requires_card=None,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN
    assert result.zero_cost_class != Z0_TRUE_FREE


def test_unknown_dominates_trial_with_no_quota_data() -> None:
    result = classify(
        OfferFacts("trial", requires_card=False, has_paid_dependencies=False),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN


def test_unknown_dominates_bounded_window_with_unknown_paid_deps() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=None,
            exhaustion_behaviours=("hard_stop",),
            available_until=_TODAY + timedelta(days=30),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN


def test_unknown_dominates_manual_upgrade_with_unknown_card() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=None,
            has_paid_dependencies=False,
            exhaustion_behaviours=("throttled", "manual_upgrade_required"),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN


def test_availability_from_only_does_not_force_z2() -> None:
    # A start date with no end date is not a bounded window.
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
            available_from=_TODAY - timedelta(days=100),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z0_TRUE_FREE


# --------------------------------------------------------------------------- #
# The OPENING gate -- available_from
# --------------------------------------------------------------------------- #
#
# WHY THIS EXISTS. The availability window had a CLOSING gate and no opening
# one. `available_from` was carried from the column (domain.py) through the ORM
# adapter (orm.py) into `OfferFacts` and then consulted by NOTHING, so an offer
# with `available_from = 2030-01-01`, classified today, reached Z0_TRUE_FREE and
# published the sentence "Usage remains $0" about an offer that does not exist
# yet. That is the product's first rule in its purest form.
#
# CALIBRATION, recorded so this is not read as bigger than it was: the defect
# was LATENT. Measured at 46a2371a, nothing outside the model wrote the column
# (0 occurrences across ingest, publish, adviser, read_api and apps/web), so no
# shipped classification was wrong because of it. It was armed, not firing.
#
# BOTH ARMS ARE TESTED BELOW, deliberately and with equal weight. A guard that
# cannot be shown to PERMIT is indistinguishable from one that broke the
# product, and here the permit arm protects against wrongly WITHHOLDING a
# genuinely free offer -- which this project weights equally with wrongly
# asserting one.


def test_z2_for_an_offer_that_is_not_available_yet() -> None:
    """DENY ARM: a start date in the future withholds Z0."""

    opens = _TODAY + timedelta(days=365)
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
            available_from=opens,
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z2_TEMPORARY_OR_CONDITIONAL
    assert result.zero_cost_class != Z0_TRUE_FREE
    # The explanation must name the date, not merely refuse.
    assert any(opens.isoformat() in c for c in result.blocking_conditions)
    assert any("does not become available" in c for c in result.blocking_conditions)
    # And it must not assert the offer is free.
    assert not any("Usage remains $0" in r for r in result.reasons)


def test_z0_still_reached_when_available_from_is_today() -> None:
    """PERMIT ARM, boundary: an offer that opens today is open today.

    The gate is strictly ``>``. If it were ``>=`` an offer would be withheld on
    the very day it became available.
    """

    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
            available_from=_TODAY,
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z0_TRUE_FREE


@pytest.mark.parametrize(
    "since",
    [None, _TODAY, _TODAY - timedelta(days=1), _TODAY - timedelta(days=3650)],
    ids=["absent", "today", "yesterday", "ten-years-ago"],
)
def test_the_opening_gate_permits_every_already_open_offer(since: date | None) -> None:
    """PERMIT ARM: absent or already-reached start dates are untouched.

    Parametrised rather than probed once, because the failure this catches is a
    'symmetrising' refactor that emits a reason for a past start date the way
    ``available_until`` does for a past end date. That would send EVERY offer
    with a start date to Z2 and withhold genuinely free offers at scale.
    """

    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
            available_from=since,
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z0_TRUE_FREE
    assert not result.blocking_conditions
    # The asymmetry, asserted rather than described: an already-open offer emits
    # NO availability sentence at all.
    assert not any("available" in r.lower() for r in result.reasons)


def test_the_opening_gate_is_what_moves_the_class_across_its_boundary() -> None:
    """The NEW property this gate introduces, stated as its own test.

    ``as_of`` does not move the class for an offer bounded only at its closing
    end -- that is pinned in ``tests/unit/test_clock_inheritance.py`` and is a
    statement about ``available_until`` alone. For an offer with a start date
    ``as_of`` DOES move the class, and that is the entire point of the gate.
    """

    opens = date(2030, 1, 1)
    facts = OfferFacts(
        "always_free",
        requires_card=False,
        has_paid_dependencies=False,
        exhaustion_behaviours=("hard_stop",),
        available_from=opens,
    )

    before = classify(facts, as_of=opens - timedelta(days=1))
    on_the_day = classify(facts, as_of=opens)
    after = classify(facts, as_of=opens + timedelta(days=1))

    assert before.zero_cost_class == Z2_TEMPORARY_OR_CONDITIONAL
    assert on_the_day.zero_cost_class == Z0_TRUE_FREE
    assert after.zero_cost_class == Z0_TRUE_FREE
    assert before.zero_cost_class != on_the_day.zero_cost_class


def test_unknown_dominates_an_offer_that_is_not_available_yet() -> None:
    """PRECEDENCE: an unknown material condition still outranks the new gate.

    Gate 4 precedes Gate 5, so a not-yet-open offer whose card requirement is
    unknown must be UNKNOWN and not be guessed into Z2.
    """

    result = classify(
        OfferFacts(
            "always_free",
            requires_card=None,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
            available_from=_TODAY + timedelta(days=30),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN
    assert result.zero_cost_class != Z0_TRUE_FREE


def test_z1_dominates_an_offer_that_is_not_available_yet() -> None:
    """PRECEDENCE: a definite billing exposure still outranks the new gate."""

    result = classify(
        OfferFacts(
            "always_free",
            requires_card=True,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
            available_from=_TODAY + timedelta(days=30),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z1_BILLING_EXPOSURE


def test_both_ends_of_a_future_window_are_reported() -> None:
    """A coherent window still ahead of us reports opening AND closing."""

    opens = _TODAY + timedelta(days=30)
    closes = _TODAY + timedelta(days=60)
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
            available_from=opens,
            available_until=closes,
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z2_TEMPORARY_OR_CONDITIONAL
    assert any("does not become available" in c for c in result.blocking_conditions)
    assert any("bounded availability window" in c for c in result.blocking_conditions)


# --------------------------------------------------------------------------- #
# A contradictory window -- available_from after available_until
# --------------------------------------------------------------------------- #
#
# The module contract promises that an unknown OR CONTRADICTORY material
# condition yields UNKNOWN. A window that opens after it ends is contradictory,
# and reporting it as merely "temporary or conditional" would publish two
# mutually contradictory sentences about one offer.


def test_a_contradictory_availability_window_is_unknown() -> None:
    opens = date(2030, 1, 1)
    closes = date(2025, 6, 1)
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
            available_from=opens,
            available_until=closes,
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN
    assert result.zero_cost_class != Z2_TEMPORARY_OR_CONDITIONAL
    assert result.zero_cost_class != Z0_TRUE_FREE
    assert any("contradictory" in c.lower() for c in result.blocking_conditions)
    # It must NOT also emit the Z2 availability sentences: Gate 4 returns first.
    assert not any("does not become available" in c for c in result.blocking_conditions)


def test_a_contradictory_window_is_unknown_at_every_moment() -> None:
    """The contradiction gate takes no clock, so no ``as_of`` can resolve it.

    Asserted rather than assumed, because a contradiction routed through a
    clock-dependent branch would reintroduce exactly the class-flips-with-time
    behaviour the rest of this engine is careful about.
    """

    facts = OfferFacts(
        "always_free",
        requires_card=False,
        has_paid_dependencies=False,
        exhaustion_behaviours=("hard_stop",),
        available_from=date(2030, 1, 1),
        available_until=date(2025, 6, 1),
    )
    for moment in (date(2024, 1, 1), date(2026, 1, 15), date(2031, 1, 1)):
        assert classify(facts, as_of=moment).zero_cost_class == UNKNOWN


def test_a_coherent_window_is_not_treated_as_contradictory() -> None:
    """The contradiction gate must not fire on equal or ordered dates."""

    same_day = date(2030, 1, 1)
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
            available_from=same_day,
            available_until=same_day,
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z2_TEMPORARY_OR_CONDITIONAL
    assert not any("contradictory" in c.lower() for c in result.blocking_conditions)


# --------------------------------------------------------------------------- #
# Z3 -- self-hosted building block
# --------------------------------------------------------------------------- #


def test_z3_for_self_hosted_open_source() -> None:
    result = classify(OfferFacts("self_hosted_open_source"), as_of=_TODAY)
    assert result.zero_cost_class == Z3_SELF_HOSTED_BUILDING_BLOCK
    assert result.blocking_conditions


def test_z3_nature_precedes_paid_dependency_gate() -> None:
    # For self-hosted OSS the "paid dependency" is the infrastructure itself;
    # its nature (Z3) is determined before the billing gate.
    result = classify(
        OfferFacts("self_hosted_open_source", requires_card=False, has_paid_dependencies=True),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == Z3_SELF_HOSTED_BUILDING_BLOCK


# --------------------------------------------------------------------------- #
# UNKNOWN -- the safety invariant: no unknown material condition yields Z0
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "offer_type",
    ["unknown", "", "   ", "totally_made_up_xyz", "TRIAL", 17],
)
def test_unrecognised_offer_type_fails_closed_before_all_other_gates(offer_type: object) -> None:
    result = classify(
        OfferFacts(
            offer_type=offer_type,  # type: ignore[arg-type] - runtime defense-in-depth
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_TODAY,
    )

    assert result.zero_cost_class == UNKNOWN
    assert result.zero_cost_class != Z0_TRUE_FREE
    assert result.reasons
    assert result.blocking_conditions
    assert all("unrecognised offer type" in text.lower() for text in result.reasons)
    assert all("safely classified" in text.lower() for text in result.blocking_conditions)


@pytest.mark.parametrize(
    "offer_type",
    [_CanonicalEqualityImpostor(), _UnhashableEqualityImpostor()],
    ids=["canonical-equality-impostor", "unhashable-equality-impostor"],
)
def test_non_string_equality_impostors_fail_closed_without_protocol_calls(
    offer_type: _CanonicalEqualityImpostor | _UnhashableEqualityImpostor,
) -> None:
    result = classify(
        OfferFacts(
            offer_type=offer_type,  # type: ignore[arg-type] - adversarial runtime value
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_TODAY,
    )

    assert result.zero_cost_class == UNKNOWN
    assert result.zero_cost_class != Z0_TRUE_FREE
    assert offer_type.eq_calls == 0
    if isinstance(offer_type, _CanonicalEqualityImpostor):
        assert offer_type.hash_calls == 0
    assert result.reasons
    assert result.blocking_conditions
    assert all("unrecognised offer type" in text.lower() for text in result.reasons)
    assert all("safely classified" in text.lower() for text in result.blocking_conditions)


def test_non_string_offer_type_is_not_coerced_to_a_canonical_string() -> None:
    offer_type = _StringCoercionImpostor()

    result = classify(
        OfferFacts(
            offer_type=offer_type,  # type: ignore[arg-type] - adversarial runtime value
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_TODAY,
    )

    assert offer_type.str_calls == 0
    assert result.zero_cost_class == UNKNOWN
    assert result.zero_cost_class != Z0_TRUE_FREE
    assert result.reasons
    assert result.blocking_conditions
    assert all("unrecognised offer type" in text.lower() for text in result.reasons)
    assert all("safely classified" in text.lower() for text in result.blocking_conditions)


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            OfferFacts(
                "always_free",
                requires_card=False,
                has_paid_dependencies=False,
                exhaustion_behaviours=("hard_stop",),
            ),
            Z0_TRUE_FREE,
        ),
        (
            OfferFacts(
                "other",
                requires_card=False,
                has_paid_dependencies=False,
                exhaustion_behaviours=("hard_stop",),
            ),
            Z0_TRUE_FREE,
        ),
        (
            OfferFacts(
                "trial",
                requires_card=False,
                has_paid_dependencies=False,
                exhaustion_behaviours=("hard_stop",),
            ),
            Z2_TEMPORARY_OR_CONDITIONAL,
        ),
        (OfferFacts("self_hosted_open_source"), Z3_SELF_HOSTED_BUILDING_BLOCK),
        (
            OfferFacts(
                "always_free",
                requires_card=True,
                has_paid_dependencies=False,
                exhaustion_behaviours=("hard_stop",),
            ),
            Z1_BILLING_EXPOSURE,
        ),
        (
            OfferFacts(
                "always_free",
                requires_card=None,
                has_paid_dependencies=False,
                exhaustion_behaviours=("hard_stop",),
            ),
            UNKNOWN,
        ),
    ],
)
def test_valid_offer_type_branch_controls(facts: OfferFacts, expected: str) -> None:
    assert classify(facts, as_of=_TODAY).zero_cost_class == expected


def test_unknown_when_card_requirement_unknown() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=None,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN
    assert result.is_zero_cost is False


def test_unknown_when_paid_dependencies_unknown() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=None,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN


def test_unknown_when_exhaustion_behaviour_unknown() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop", "unknown"),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN


def test_unknown_when_no_quota_data() -> None:
    result = classify(
        OfferFacts("always_free", requires_card=False, has_paid_dependencies=False),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN
    assert any("no quota data" in c.lower() for c in result.blocking_conditions)


def test_unknown_lists_every_unknown_condition() -> None:
    result = classify(
        OfferFacts("always_free", requires_card=None, has_paid_dependencies=None),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN
    # card unknown + paid-deps unknown + no quota data == 3 blocking conditions
    assert len(result.blocking_conditions) == 3


def test_unrecognised_exhaustion_behaviour_is_unknown_not_z0() -> None:
    result = classify(
        OfferFacts(
            "always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop", "melts"),
        ),
        as_of=_TODAY,
    )
    assert result.zero_cost_class == UNKNOWN
    assert any("melts" in c for c in result.blocking_conditions)


def test_no_input_combination_yields_z0_with_an_unknown_material_condition() -> None:
    # Exhaustive guard over the safety invariant across a representative grid.
    for card in (None, False):
        for deps in (None, False):
            for beh in ((), ("hard_stop",), ("unknown",), ("hard_stop", "unknown")):
                facts = OfferFacts(
                    "always_free",
                    requires_card=card,
                    has_paid_dependencies=deps,
                    exhaustion_behaviours=beh,
                )
                result = classify(facts, as_of=_TODAY)
                has_unknown = card is None or deps is None or beh == () or "unknown" in beh
                if has_unknown:
                    assert result.zero_cost_class != Z0_TRUE_FREE, facts


# --------------------------------------------------------------------------- #
# Determinism, helpers, and result contract
# --------------------------------------------------------------------------- #


def test_classification_is_deterministic() -> None:
    facts = OfferFacts(
        "always_free",
        requires_card=False,
        has_paid_dependencies=False,
        exhaustion_behaviours=("throttled",),
    )
    first = classify(facts, as_of=_TODAY)
    second = classify(facts, as_of=_TODAY)
    assert first == second


def test_result_is_immutable() -> None:
    result = classify(OfferFacts("self_hosted_open_source"), as_of=_TODAY)
    with pytest.raises(FrozenInstanceError):
        result.zero_cost_class = "hacked"  # type: ignore[misc]


def test_summarise_counts_by_class() -> None:
    results = [
        ClassificationResult(Z0_TRUE_FREE),
        ClassificationResult(Z0_TRUE_FREE),
        ClassificationResult(Z1_BILLING_EXPOSURE),
        ClassificationResult(UNKNOWN),
    ]
    counts = summarise(results)
    assert counts[Z0_TRUE_FREE] == 2
    assert counts[Z1_BILLING_EXPOSURE] == 1
    assert counts[UNKNOWN] == 1
    assert counts[Z3_SELF_HOSTED_BUILDING_BLOCK] == 0


# --------------------------------------------------------------------------- #
# Read-only ORM adapter (transient in-memory instances -- no database)
# --------------------------------------------------------------------------- #


def _make_offer(
    offer_type: str,
    *,
    requires_card,
    has_paid_dependencies,
    behaviours: tuple[str, ...],
    version_number: int = 1,
) -> Offer:
    offer = Offer(
        offer_type=offer_type,
        zero_cost_class="UNKNOWN",
        requires_card=requires_card,
        has_paid_dependencies=has_paid_dependencies,
    )
    version = OfferVersion(
        offer_type=offer_type,
        zero_cost_class="UNKNOWN",
        version_number=version_number,
        content_hash="deadbeef",
    )
    version.quotas = [
        Quota(metric=f"metric_{i}", exhaustion_behaviour=b) for i, b in enumerate(behaviours)
    ]
    offer.versions = [version]
    return offer


def test_classify_offer_matches_pure_function() -> None:
    offer = _make_offer(
        "always_free", requires_card=False, has_paid_dependencies=False, behaviours=("hard_stop",)
    )
    result = classify_offer(offer, as_of=_TODAY)
    assert result.zero_cost_class == Z0_TRUE_FREE


def test_classify_offer_billing_exposure() -> None:
    offer = _make_offer(
        "always_free", requires_card=True, has_paid_dependencies=False, behaviours=("hard_stop",)
    )
    assert classify_offer(offer, as_of=_TODAY).zero_cost_class == Z1_BILLING_EXPOSURE


def test_classify_offer_no_versions_is_unknown() -> None:
    offer = Offer(
        offer_type="always_free",
        zero_cost_class="UNKNOWN",
        requires_card=False,
        has_paid_dependencies=False,
    )
    offer.versions = []
    assert classify_offer(offer, as_of=_TODAY).zero_cost_class == UNKNOWN


def test_classify_offer_selects_latest_version() -> None:
    offer = Offer(
        offer_type="always_free",
        zero_cost_class="UNKNOWN",
        requires_card=False,
        has_paid_dependencies=False,
    )
    v1 = OfferVersion(
        offer_type="always_free", zero_cost_class="UNKNOWN", version_number=1, content_hash="a"
    )
    v1.quotas = [Quota(metric="m", exhaustion_behaviour="automatic_billing")]
    v2 = OfferVersion(
        offer_type="always_free", zero_cost_class="UNKNOWN", version_number=2, content_hash="b"
    )
    v2.quotas = [Quota(metric="m", exhaustion_behaviour="hard_stop")]
    offer.versions = [v1, v2]
    # Latest version (v2) has a safe behaviour -> Z0.
    assert classify_offer(offer, as_of=_TODAY).zero_cost_class == Z0_TRUE_FREE
    # Explicitly classifying against v1 surfaces the billing exposure.
    assert classify_offer(offer, v1, as_of=_TODAY).zero_cost_class == Z1_BILLING_EXPOSURE
