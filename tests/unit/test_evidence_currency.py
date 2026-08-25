"""Evidence currency: the pure abstraction, exercised in both directions.

Every assertion here is paired. A guard that only ever refuses is as broken as
one that only ever permits -- a wrongly-withdrawn free offer is a defect of the
same severity as a stale one that ships -- so each refusal below has a matching
case proving the same code path still permits a genuinely current claim.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.read_api.currency import (
    ANCHOR_OFFER_VERSION,
    UNCHECKED,
    UNSUPPORTED_CONFIDENCE_LABEL,
    EvidenceCurrency,
    assess_currency,
    confidence_label_for,
    currency_for,
    is_publishable_free_claim,
    worst,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _fetched(days: float) -> datetime:
    return NOW - timedelta(days=days)


# --------------------------------------------------------------------------- #
# assess_currency: the three verdicts                                          #
# --------------------------------------------------------------------------- #


def test_evidence_inside_its_window_is_current() -> None:
    verdict = assess_currency(_fetched(1), NOW, "weekly")
    assert verdict.checked is True
    assert verdict.stale is False
    assert verdict.current is True
    assert verdict.reason() is None


def test_evidence_past_its_window_is_stale() -> None:
    verdict = assess_currency(_fetched(30), NOW, "weekly")
    assert verdict.checked is True
    assert verdict.stale is True
    assert verdict.current is False
    assert "no longer known to be current" in (verdict.reason() or "")


def test_evidence_with_no_fetch_time_is_unchecked_not_fresh_and_not_stale() -> None:
    """The declaration-only shape: no snapshot, so no statement is available.

    This is the case that must never quietly read as "fresh". It is equally
    important that it does not read as "stale": absence of a timestamp is not
    evidence of expiry, and reporting it as expired would be a guess in the
    other direction.
    """

    verdict = assess_currency(None, NOW, "weekly")
    assert verdict is UNCHECKED
    assert verdict.checked is False
    assert verdict.stale is False
    assert verdict.current is False
    assert "cannot be established" in (verdict.reason() or "")


def test_a_stale_verdict_cannot_be_constructed_without_having_been_checked() -> None:
    """The unrepresentable state is rejected, not silently normalised."""

    with pytest.raises(ValueError, match="without having been checked"):
        EvidenceCurrency(stale=True, checked=False)


def test_an_unparseable_schedule_still_counts_as_checked() -> None:
    """A missing schedule falls back to the documented default window.

    It does NOT become "unchecked": we do have a fetch time, so a statement
    about currency is available and must be made.
    """

    fresh = assess_currency(_fetched(1), NOW, "not-a-schedule")
    stale = assess_currency(_fetched(30), NOW, "not-a-schedule")
    assert fresh.checked is True and fresh.stale is False
    assert stale.checked is True and stale.stale is True


# --------------------------------------------------------------------------- #
# freshness is recomputed, never frozen                                        #
# --------------------------------------------------------------------------- #


def test_freshness_decays_with_age_rather_than_being_a_publish_time_constant() -> None:
    """The aggravating fact this module exists to remove.

    A publish-time constant reports 1.0 forever. Recomputed, the same evidence
    reports a falling figure and finally 0.0 -- and the *same* function that
    still returns a high figure for genuinely fresh evidence.
    """

    just_fetched = assess_currency(_fetched(0), NOW, "weekly").freshness()
    half_way = assess_currency(_fetched(3.5), NOW, "weekly").freshness()
    expired = assess_currency(_fetched(3650), NOW, "weekly").freshness()

    assert just_fetched == pytest.approx(1.0)
    assert half_way == pytest.approx(0.5)
    assert expired == 0.0
    assert just_fetched > half_way > expired


def test_unchecked_freshness_is_none_not_zero() -> None:
    """An absent measurement, not a bad score.

    0.0 would say "we looked and it is completely stale". ``None`` says "no
    measurement exists", which is the truth and is what stops a caller
    averaging a fabricated zero into a provider-level figure.
    """

    assert UNCHECKED.freshness() is None


# --------------------------------------------------------------------------- #
# worst(): a claim is only as current as its weakest support                   #
# --------------------------------------------------------------------------- #


def test_worst_of_all_current_evidence_is_current() -> None:
    combined = worst(
        [assess_currency(_fetched(1), NOW, "weekly"), assess_currency(_fetched(2), NOW, "weekly")]
    )
    assert combined.current is True


def test_one_stale_source_makes_the_whole_claim_stale() -> None:
    combined = worst(
        [assess_currency(_fetched(1), NOW, "weekly"), assess_currency(_fetched(99), NOW, "weekly")]
    )
    assert combined.stale is True
    assert combined.current is False


def test_one_uncheckable_source_stops_us_asserting_currency() -> None:
    combined = worst([assess_currency(_fetched(1), NOW, "weekly"), UNCHECKED])
    assert combined.checked is False
    assert combined.current is False


def test_worst_of_nothing_is_unchecked_not_current() -> None:
    """An offer version with no evidence rows at all cannot back a free claim."""

    assert worst([]) is UNCHECKED


def test_stale_outranks_unchecked_so_the_reason_is_the_specific_one() -> None:
    combined = worst([assess_currency(_fetched(99), NOW, "weekly"), UNCHECKED])
    assert combined.stale is True
    assert "past its" in (combined.reason() or "")


# --------------------------------------------------------------------------- #
# The gate + the confidence cap                                                #
# --------------------------------------------------------------------------- #


def test_free_claim_is_publishable_only_while_its_evidence_is_current() -> None:
    assert is_publishable_free_claim(assess_currency(_fetched(1), NOW, "weekly")) is True
    assert is_publishable_free_claim(assess_currency(_fetched(99), NOW, "weekly")) is False
    assert is_publishable_free_claim(UNCHECKED) is False


def test_confidence_is_capped_when_support_is_gone_and_untouched_when_it_is_not() -> None:
    current = assess_currency(_fetched(1), NOW, "weekly")
    expired = assess_currency(_fetched(99), NOW, "weekly")

    # Untouched while current -- this only ever removes unearned confidence.
    for label in ("high", "medium", "low", "unknown"):
        assert confidence_label_for(label, current) == label

    assert confidence_label_for("high", expired) == UNSUPPORTED_CONFIDENCE_LABEL
    assert confidence_label_for("high", UNCHECKED) == UNSUPPORTED_CONFIDENCE_LABEL


def test_capped_label_is_unknown_rather_than_low() -> None:
    """ "Low" asserts a weak measurement; an expired claim has none at all."""

    assert UNSUPPORTED_CONFIDENCE_LABEL == "unknown"


# --------------------------------------------------------------------------- #
# currency_for(): a caller with no clock fails closed                          #
# --------------------------------------------------------------------------- #


def test_missing_index_or_missing_anchor_fails_closed_to_unchecked() -> None:
    """An un-threaded call site must not silently re-acquire "always fresh"."""

    current = assess_currency(_fetched(1), NOW, "weekly")
    index = {(ANCHOR_OFFER_VERSION, 7): current}

    assert currency_for(ANCHOR_OFFER_VERSION, 7, index) is current
    assert currency_for(ANCHOR_OFFER_VERSION, 7, None) is UNCHECKED
    assert currency_for(ANCHOR_OFFER_VERSION, 999, index) is UNCHECKED
    assert currency_for(ANCHOR_OFFER_VERSION, None, index) is UNCHECKED
