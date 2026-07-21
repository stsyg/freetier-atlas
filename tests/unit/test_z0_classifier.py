"""Truth-table tests for the Z0 classification engine (offline, pure).

Covers every decision gate, every exhaustion behaviour (safe / billing /
conditional / unknown), boundary and contradictory inputs, the no-quota case,
the read-only ORM adapter, and the central safety invariant: no unknown
material condition may ever yield Z0.
"""

from __future__ import annotations

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
    UNKNOWN_EXHAUSTION,
    _offer_types_recognised,
    _partition_covers_vocabulary,
)
from app.models.domain import Offer, OfferVersion, Quota
from app.models.vocab import EXHAUSTION_BEHAVIOURS, ZERO_COST_CLASSES

_TODAY = date(2026, 1, 15)


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
# Z0 -- true $0
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "offer_type", ["always_free", "recurring_quota", "personal_use_free", "other"]
)
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


@pytest.mark.parametrize(
    "offer_type",
    [
        "trial",
        "new_customer_credit",
        "startup_program",
        "student_program",
        "open_source_program",
        "hackathon_promotion",
    ],
)
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
