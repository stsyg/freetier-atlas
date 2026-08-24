"""The two F008 materiality rulings, proven in both directions (obsB + obsC).

Two defects on ``main``, both reproduced here before they are fixed:

**f008-obsB.** A provider declares ``verified_free``, the catalogue publishes a
Z0 offer, and the snapshot behind it then goes past its refresh window. The
derivation correctly degrades to ``stale`` -- but ``(verified_free, stale)`` was
not material, so no review item was raised, and ``effective_state`` returned the
declaration, so the catalogue kept **presenting the offer as free on evidence
that had expired**. Measured against the merged tree, all five published free
claims the catalogue has enter exactly this state the moment their snapshots
age, and ``main`` raises zero review items for them.

**f008-obsC.** A provider declares ``verified_free`` with provenance, ingest
never publishes an offer in that category, the derivation is ``unknown``, and
``unknown`` was never material -- so the claim displayed forever with nothing
telling a human to reconcile it.

The two rulings get *different* remedies, and that asymmetry is the point:

* obsB downgrades the display, because the evidence the claim rested on has
  **expired**. That is unsupported, and unsupported free claims are the one
  thing this product must never publish.
* obsC does **not** downgrade the display, because an absent publication is not
  a refutation. The publication gate withholds offers by design -- five of the
  six merged providers publish nothing at all -- so 93 of 98 pairs derive
  ``unknown`` for reasons that say nothing about whether the declaration is
  true. Suppressing a true free claim is a defect too.

Every guard below is paired with a control that must NOT fire, because a guard
that only fires in the direction its author feared is a guard its author has not
understood.
"""

from __future__ import annotations

import pytest
from app.read_api.coverage import (
    MATERIAL_MISMATCH_DISPLAY,
    MATERIAL_MISMATCHES,
    UNKNOWN,
    CoverageSignals,
    derive_coverage_state,
    effective_state,
    is_material_mismatch,
)

# The five declarations that are not a positive free claim. A derived state that
# is material for these would be the queue flood slice S2 refused, so they are
# the control group for both rulings.
NON_FREE_DECLARATIONS = ("offered_no_z0", "not_offered", "incomplete", "stale", "conflicting")


def _fresh_free() -> CoverageSignals:
    return CoverageSignals(published_offer_count=1, free_offer_count=1)


def _stale_free() -> CoverageSignals:
    return CoverageSignals(published_offer_count=1, free_offer_count=1, has_stale_evidence=True)


# --------------------------------------------------------------------------- #
# f008-obsB -- a stale snapshot must not sustain a published free claim        #
# --------------------------------------------------------------------------- #


def test_the_defect_reproduces_the_derivation_that_exposes_it() -> None:
    """Precondition: the derivation already reports the expiry. It always did.

    obsB was never a derivation bug. Pinning this here means a later change that
    stops deriving ``stale`` cannot make the rulings below vacuously pass.
    """

    assert derive_coverage_state(_stale_free()) == "stale"
    assert derive_coverage_state(_fresh_free()) == "verified_free"


def test_a_stale_free_claim_is_material() -> None:
    """UNSAFE DIRECTION: the expired claim now raises a review item."""

    assert is_material_mismatch("verified_free", "stale")
    assert ("verified_free", "stale") in MATERIAL_MISMATCHES


def test_a_stale_free_claim_stops_being_displayed_as_free() -> None:
    """UNSAFE DIRECTION: the display is where the product harm lands."""

    displayed = effective_state("verified_free", "stale")
    assert displayed != "verified_free"
    assert displayed == "stale"


def test_a_stale_free_claim_is_not_labelled_a_disagreement() -> None:
    """`conflicting` would be a small guess of its own: nothing here disagrees.

    The generic collapse would have said sources conflict. They do not -- the
    snapshot expired, which is exactly what ``stale`` means and exactly what the
    web vocabulary already tells a reader ("past its refresh window").
    """

    assert effective_state("verified_free", "stale") != "conflicting"
    assert MATERIAL_MISMATCH_DISPLAY[("verified_free", "stale")] == "stale"


def test_a_fresh_free_claim_over_its_own_free_offer_is_untouched() -> None:
    """SAFE DIRECTION: the legitimate case must not be flagged or downgraded."""

    assert derive_coverage_state(_fresh_free()) == "verified_free"
    assert not is_material_mismatch("verified_free", "verified_free")
    assert effective_state("verified_free", "verified_free") == "verified_free"


@pytest.mark.parametrize("declared", NON_FREE_DECLARATIONS)
def test_staleness_only_unseats_a_free_claim(declared: str) -> None:
    """SAFE DIRECTION: the ruling is about free claims, not about staleness.

    ``not_offered`` over a derived ``stale`` was already material before this
    slice (there are published offers despite the denial) and must stay so, with
    its existing ``conflicting`` display -- that pair really is a disagreement.
    Every other non-free declaration must stay unflagged.
    """

    if declared == "not_offered":
        assert is_material_mismatch(declared, "stale")
        assert effective_state(declared, "stale") == "conflicting"
    else:
        assert not is_material_mismatch(declared, "stale")
        assert effective_state(declared, "stale") == declared


# --------------------------------------------------------------------------- #
# f008-obsC -- a free claim the catalogue has never corroborated               #
# --------------------------------------------------------------------------- #


def test_an_uncorroborated_free_claim_is_material() -> None:
    """UNSAFE DIRECTION: it now reaches a human instead of sitting forever."""

    assert is_material_mismatch("verified_free", UNKNOWN)
    assert ("verified_free", UNKNOWN) in MATERIAL_MISMATCHES


def test_an_uncorroborated_free_claim_still_displays_the_declaration() -> None:
    """SAFE DIRECTION, and the reason obsC is not obsB.

    A ``verified_free`` declaration is provenance-backed: the config loader
    rejects an offered category with no source. An absent publication refutes
    nothing, so downgrading here would suppress a true free claim because the
    publication gate is conservative. Raising the review item is the remedy;
    changing the public claim is not.
    """

    assert effective_state("verified_free", UNKNOWN) == "verified_free"
    assert MATERIAL_MISMATCH_DISPLAY[("verified_free", UNKNOWN)] == "verified_free"


@pytest.mark.parametrize("declared", NON_FREE_DECLARATIONS)
def test_absence_of_ingest_flags_nothing_but_a_free_claim(declared: str) -> None:
    """SAFE DIRECTION: this is where the rejected broad rule's 91 items live.

    Measured on the merged tree: 93 of 98 pairs derive ``unknown``. Making all
    of them material is the flood slice S2 refused and this slice re-measured
    and refused again. Only the free claim is exempted.
    """

    assert not is_material_mismatch(declared, UNKNOWN)
    assert effective_state(declared, UNKNOWN) == declared


def test_an_undeclared_pair_is_still_unknown_and_still_reviewable() -> None:
    """A missing declaration must not be read as a free claim."""

    assert effective_state(None, UNKNOWN) == UNKNOWN
    assert not is_material_mismatch(None, UNKNOWN)
    assert is_material_mismatch(None, "verified_free")


# --------------------------------------------------------------------------- #
# Regression: nothing that was material before stopped being material          #
# --------------------------------------------------------------------------- #


PRE_EXISTING_MATERIAL: tuple[tuple[str, str], ...] = (
    (UNKNOWN, "verified_free"),
    (UNKNOWN, "offered_no_z0"),
    ("not_offered", "verified_free"),
    ("not_offered", "offered_no_z0"),
    ("not_offered", "incomplete"),
    ("not_offered", "stale"),
    ("not_offered", "conflicting"),
    ("verified_free", "offered_no_z0"),
    ("verified_free", "conflicting"),
    ("offered_no_z0", "verified_free"),
    ("offered_no_z0", "conflicting"),
)


@pytest.mark.parametrize(("declared", "derived"), PRE_EXISTING_MATERIAL)
def test_every_pre_existing_material_pair_survives(declared: str, derived: str) -> None:
    """No existing guard may be weakened to make the two new ones fit."""

    assert (declared, derived) in MATERIAL_MISMATCHES
    assert is_material_mismatch(declared, derived)
    assert effective_state(declared, derived) == "conflicting"


def test_the_material_set_grew_by_exactly_the_two_ruled_pairs() -> None:
    """The set is enumerated, so an accidental third addition is visible."""

    assert MATERIAL_MISMATCHES == frozenset(PRE_EXISTING_MATERIAL) | {
        ("verified_free", "stale"),
        ("verified_free", UNKNOWN),
    }
    assert len(MATERIAL_MISMATCHES) == len(PRE_EXISTING_MATERIAL) + 2


def test_only_the_two_ruled_pairs_escape_the_conflicting_collapse() -> None:
    """A display override is a licence to publish something other than
    ``conflicting`` for a contradiction, so the table must stay tiny and every
    entry must name a pair that is genuinely material."""

    assert set(MATERIAL_MISMATCH_DISPLAY) == {
        ("verified_free", "stale"),
        ("verified_free", UNKNOWN),
    }
    for pair in MATERIAL_MISMATCH_DISPLAY:
        assert pair in MATERIAL_MISMATCHES


def test_the_display_table_is_not_writable() -> None:
    """A module-level mapping a caller can mutate is a rule anyone can rewrite."""

    with pytest.raises(TypeError):
        MATERIAL_MISMATCH_DISPLAY[("unknown", "verified_free")] = "verified_free"  # type: ignore[index]
