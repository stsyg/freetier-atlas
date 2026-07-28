"""Pure coverage-derivation truth table (F008 slice S2). No database required.

The single most important assertion in this module is that
:func:`derive_coverage_state` can *never* return ``not_offered``. Before this
slice the read API guessed ``not_offered`` from a zero published-offer count,
conflating "we have not verified this" with "the provider does not offer it".
Re-introducing that guess must turn these tests red.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.models.vocab import COVERAGE_STATES
from app.read_api.coverage import (
    DERIVABLE_STATES,
    MATERIAL_MISMATCHES,
    UNKNOWN,
    CoverageSignals,
    derive_coverage_state,
    describe_mismatch,
    effective_state,
    is_material_mismatch,
    mismatch_detail,
    signals_from_offers,
)

from tests.support.coverage import assert_declarations_match_signals


class _Offer:
    def __init__(self, zero_cost_class: str | None) -> None:
        self.zero_cost_class = zero_cost_class


# --------------------------------------------------------------------------- #
# The vocabulary                                                              #
# --------------------------------------------------------------------------- #


def test_seven_states_exactly() -> None:
    assert COVERAGE_STATES == (
        "verified_free",
        "offered_no_z0",
        "not_offered",
        "incomplete",
        "stale",
        "conflicting",
        "unknown",
    )


def test_not_offered_is_declarable_but_never_derivable() -> None:
    assert "not_offered" in COVERAGE_STATES
    assert "not_offered" not in DERIVABLE_STATES
    assert DERIVABLE_STATES == set(COVERAGE_STATES) - {"not_offered"}


# --------------------------------------------------------------------------- #
# Truth table                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        # Nothing published -> we have not verified this. NOT 'not_offered'.
        (CoverageSignals(), UNKNOWN),
        # A published Z0 offer is a verified free offer.
        (CoverageSignals(published_offer_count=1, free_offer_count=1), "verified_free"),
        (CoverageSignals(published_offer_count=3, free_offer_count=1), "verified_free"),
        # Published, classified, none free -> the category exists but has no Z0.
        (CoverageSignals(published_offer_count=2), "offered_no_z0"),
        # Published but unclassifiable -> we cannot assert 'no Z0 here' either.
        (
            CoverageSignals(published_offer_count=1, unclassified_offer_count=1),
            "incomplete",
        ),
        (
            CoverageSignals(published_offer_count=2, unclassified_offer_count=1),
            "incomplete",
        ),
        # Staleness outranks an optimistic free claim.
        (
            CoverageSignals(published_offer_count=1, free_offer_count=1, has_stale_evidence=True),
            "stale",
        ),
        # An unresolved contradiction outranks everything.
        (
            CoverageSignals(
                published_offer_count=1,
                free_offer_count=1,
                has_stale_evidence=True,
                has_pending_contradiction=True,
            ),
            "conflicting",
        ),
        # A contradiction with nothing published is still a contradiction.
        (CoverageSignals(has_pending_contradiction=True), "conflicting"),
        # Stale evidence with nothing published is just 'unknown' -- there is no
        # published claim to have gone stale.
        (CoverageSignals(has_stale_evidence=True), UNKNOWN),
    ],
)
def test_derivation_truth_table(signals: CoverageSignals, expected: str) -> None:
    assert derive_coverage_state(signals) == expected


@pytest.mark.parametrize(
    "signals",
    [
        CoverageSignals(),
        CoverageSignals(published_offer_count=1),
        CoverageSignals(published_offer_count=1, free_offer_count=1),
        CoverageSignals(published_offer_count=1, unclassified_offer_count=1),
        CoverageSignals(has_stale_evidence=True),
        CoverageSignals(has_pending_contradiction=True),
        CoverageSignals(published_offer_count=9, free_offer_count=9, has_stale_evidence=True),
    ],
)
def test_derivation_never_returns_not_offered(signals: CoverageSignals) -> None:
    state = derive_coverage_state(signals)
    assert state != "not_offered"
    assert state in DERIVABLE_STATES


def test_zero_published_is_never_evidence_of_absence() -> None:
    """The exact guess F008 slice S2 deleted."""

    for stale in (False, True):
        assert derive_coverage_state(CoverageSignals(has_stale_evidence=stale)) == UNKNOWN


def test_signals_reject_impossible_tallies() -> None:
    with pytest.raises(ValueError):
        CoverageSignals(published_offer_count=1, free_offer_count=2)
    with pytest.raises(ValueError):
        CoverageSignals(published_offer_count=1, unclassified_offer_count=2)
    with pytest.raises(ValueError):
        CoverageSignals(published_offer_count=-1)


# --------------------------------------------------------------------------- #
# Tallying offers                                                             #
# --------------------------------------------------------------------------- #


def test_signals_from_offers_tallies_classes() -> None:
    signals = signals_from_offers(
        [
            _Offer("Z0_TRUE_FREE"),
            _Offer("Z1_BILLING_EXPOSURE"),
            _Offer("UNKNOWN"),
            _Offer(None),
        ]
    )
    assert signals.published_offer_count == 4
    assert signals.free_offer_count == 1
    assert signals.unclassified_offer_count == 2
    assert derive_coverage_state(signals) == "verified_free"


def test_signals_from_offers_with_no_offers_is_unknown() -> None:
    assert derive_coverage_state(signals_from_offers([])) == UNKNOWN


# --------------------------------------------------------------------------- #
# Mismatch rules (Q9-A)                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("declared", "derived"),
    [
        ("unknown", "verified_free"),
        ("unknown", "offered_no_z0"),
        ("not_offered", "verified_free"),
        ("not_offered", "offered_no_z0"),
        ("verified_free", "offered_no_z0"),
        ("offered_no_z0", "verified_free"),
    ],
)
def test_material_mismatches(declared: str, derived: str) -> None:
    assert is_material_mismatch(declared, derived)
    assert effective_state(declared, derived) == "conflicting"


@pytest.mark.parametrize(
    ("declared", "derived"),
    [
        # An evidence-backed declaration may legitimately run ahead of ingestion.
        ("verified_free", "unknown"),
        ("offered_no_z0", "unknown"),
        ("not_offered", "unknown"),
        # A declared hedge is already honest about uncertainty.
        ("incomplete", "verified_free"),
        ("stale", "verified_free"),
        ("conflicting", "verified_free"),
        # Agreement is never a mismatch.
        ("verified_free", "verified_free"),
        ("unknown", "unknown"),
    ],
)
def test_non_material_pairs(declared: str, derived: str) -> None:
    assert not is_material_mismatch(declared, derived)


def test_derived_unknown_is_never_material() -> None:
    for declared in COVERAGE_STATES:
        assert not is_material_mismatch(declared, UNKNOWN)
    assert all(derived != UNKNOWN for _declared, derived in MATERIAL_MISMATCHES)


def test_undeclared_pair_is_unknown_and_never_conflicting() -> None:
    # An undeclared pair displays as 'unknown' even when the catalogue has
    # evidence, but it is still a material mismatch worth reviewing.
    assert effective_state(None, "verified_free") == UNKNOWN
    assert is_material_mismatch(None, "verified_free")
    assert not is_material_mismatch(None, UNKNOWN)


def test_effective_state_prefers_the_declaration_when_compatible() -> None:
    assert effective_state("not_offered", UNKNOWN) == "not_offered"
    assert effective_state("verified_free", "verified_free") == "verified_free"
    assert effective_state("incomplete", "verified_free") == "incomplete"


def test_mismatch_detail_is_actionable() -> None:
    signals = CoverageSignals(published_offer_count=2, free_offer_count=1)
    detail = mismatch_detail(
        provider_slug="cloudflare",
        category_slug="serverless-functions",
        declared_state="unknown",
        derived_state="verified_free",
        signals=signals,
    )
    message = describe_mismatch(detail)
    assert "cloudflare/serverless-functions" in message
    assert "unknown" in message
    assert "verified_free" in message
    assert "2 published" in message


# --------------------------------------------------------------------------- #
# The reusable Wave-3 helper (database-free variant)                          #
# --------------------------------------------------------------------------- #


def _cloudflare_config():
    from app.config.loader import load_and_validate

    root = Path(__file__).resolve().parents[2]
    return load_and_validate(
        str(root / "config" / "examples" / "providers" / "cloudflare.example.yaml")
    )


def test_helper_passes_when_the_catalogue_agrees_with_the_declarations() -> None:
    """The shipped config must survive its own evidence-backed expectations."""

    config = _cloudflare_config()
    signals = {
        slug: CoverageSignals(published_offer_count=1, free_offer_count=1)
        for slug, entry in config.coverage.items()
        if entry.state == "verified_free"
    }
    signals.update(
        {
            slug: CoverageSignals(published_offer_count=1, free_offer_count=0)
            for slug, entry in config.coverage.items()
            if entry.state == "offered_no_z0"
        }
    )
    assert_declarations_match_signals(config, signals)


def test_helper_fails_an_unknown_declared_over_a_published_free_offer() -> None:
    """The exact failure mode the six provider slices must not be able to ship."""

    config = _cloudflare_config()
    hidden = next(slug for slug, entry in config.coverage.items() if entry.state == "unknown")

    with pytest.raises(AssertionError) as exc:
        assert_declarations_match_signals(
            config,
            {hidden: CoverageSignals(published_offer_count=3, free_offer_count=2)},
        )

    message = str(exc.value)
    assert hidden in message
    assert "unknown" in message
    assert "verified_free" in message


def test_helper_fails_a_not_offered_declared_over_a_published_offer() -> None:
    config = _cloudflare_config()
    denied = next(slug for slug, entry in config.coverage.items() if entry.state == "not_offered")

    with pytest.raises(AssertionError, match="not_offered"):
        assert_declarations_match_signals(
            config,
            {denied: CoverageSignals(published_offer_count=2, free_offer_count=1)},
        )


def test_helper_ignores_categories_with_nothing_published() -> None:
    """A slice may assert only the pairs it cares about; silence is not a failure."""

    assert_declarations_match_signals(_cloudflare_config(), {})
