"""Unit tests for deterministic confidence scoring (F005 slice 2)."""

from __future__ import annotations

from datetime import timedelta

from app.publish.confidence import (
    WEIGHTS,
    ConfidenceSignals,
    completeness,
    compute_confidence,
    freshness_ratio,
    signals_as_material_fact,
)
from app.publish.revalidate import revalidate_quotas

COMPLETE_FACTS = {
    "service": "Cloudflare Workers",
    "offer_type": "always_free",
    "requires_card": False,
    "has_paid_dependencies": False,
    "requests_per_day": "100,000/day",
    "exhaustion_behaviour": "request_rejected",
}


def _revalidation(facts):
    return revalidate_quotas(
        facts, exhaustion_behaviour=facts.get("exhaustion_behaviour", "unknown")
    )


def test_weights_sum_to_one() -> None:
    assert round(sum(WEIGHTS.values()), 6) == 1.0


def test_completeness_full_when_all_core_fields_known() -> None:
    ratio, missing = completeness(COMPLETE_FACTS, _revalidation(COMPLETE_FACTS))
    assert ratio == 1.0
    assert missing == ()


def test_completeness_penalises_unknown_boolean_gate() -> None:
    facts = dict(COMPLETE_FACTS)
    facts["requires_card"] = None
    ratio, missing = completeness(facts, _revalidation(facts))
    assert ratio < 1.0
    assert "requires_card" in missing


def test_completeness_penalises_missing_quota() -> None:
    facts = {
        "service": "x",
        "offer_type": "always_free",
        "requires_card": False,
        "has_paid_dependencies": False,
        "exhaustion_behaviour": "hard_stop",
    }
    ratio, missing = completeness(facts, _revalidation(facts))
    assert "quotas_present" in missing
    assert ratio < 1.0


def test_freshness_ratio_bounds() -> None:
    assert freshness_ratio(timedelta(0), timedelta(days=1)) == 1.0
    assert freshness_ratio(timedelta(days=1), timedelta(days=1)) == 0.0
    assert freshness_ratio(timedelta(days=2), timedelta(days=1)) == 0.0
    assert 0.0 < freshness_ratio(timedelta(hours=12), timedelta(days=1)) < 1.0


def test_freshness_ratio_zero_window_is_zero() -> None:
    assert freshness_ratio(timedelta(hours=1), timedelta(0)) == 0.0


def test_full_signals_score_one_and_clear_threshold() -> None:
    ratio, _ = completeness(COMPLETE_FACTS, _revalidation(COMPLETE_FACTS))
    signals = ConfidenceSignals(
        official=True,
        evidence_backed=True,
        deterministic=True,
        reproducible=True,
        no_contradiction=True,
        completeness=ratio,
        freshness=1.0,
    )
    assert compute_confidence(signals) == 1.0


def test_missing_evidence_drops_below_automatic_threshold() -> None:
    signals = ConfidenceSignals(True, False, True, True, True, 1.0, 1.0)
    assert compute_confidence(signals) < 0.90


def test_compute_confidence_is_deterministic() -> None:
    signals = ConfidenceSignals(True, True, True, False, True, 0.83, 0.5)
    assert compute_confidence(signals) == compute_confidence(signals)


def test_score_is_clamped_and_rounded() -> None:
    signals = ConfidenceSignals(True, True, True, True, True, 1.0, 1.0)
    score = compute_confidence(signals)
    assert 0.0 <= score <= 1.0
    assert score == round(score, 4)


def test_signals_snapshot_is_json_safe() -> None:
    signals = ConfidenceSignals(True, True, False, True, True, 0.8333, 0.5)
    snapshot = signals_as_material_fact(signals)
    assert snapshot["official"] is True
    assert snapshot["deterministic"] is False
    assert isinstance(snapshot["completeness"], float)
