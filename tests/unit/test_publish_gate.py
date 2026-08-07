"""Unit tests for the deterministic publication gate routing (F005 slice 2)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.ingest.scan import _content_hash
from app.publish.gate import (
    PUBLISH,
    REVIEW,
    WITHHOLD,
    GateConditions,
    evaluate_gate,
)
from app.publish.publisher import _build_conditions
from app.publish.revalidate import revalidate_quotas

AUTO = 0.90
UNCERTAIN = 0.70


def _all_pass(confidence: float = 1.0) -> GateConditions:
    return GateConditions(
        official=True,
        schema_complete=True,
        deterministic=True,
        reproducible=True,
        evidence_backed=True,
        no_contradiction=True,
        fresh=True,
        confidence=confidence,
    )


def _decide(conditions: GateConditions):
    return evaluate_gate(conditions, automatic_threshold=AUTO, uncertain_threshold=UNCERTAIN)


def test_all_conditions_and_high_confidence_publishes() -> None:
    assert _decide(_all_pass(0.95)).decision == PUBLISH


def test_exactly_at_threshold_publishes() -> None:
    assert _decide(_all_pass(0.90)).decision == PUBLISH


def test_unofficial_is_withheld_never_reviewed() -> None:
    cond = _all_pass(1.0)
    cond = GateConditions(**{**cond.__dict__, "official": False})
    decision = _decide(cond)
    assert decision.decision == WITHHOLD
    assert any("official" in r for r in decision.reasons)


def test_unevidenced_is_withheld() -> None:
    cond = GateConditions(**{**_all_pass(1.0).__dict__, "evidence_backed": False})
    assert _decide(cond).decision == WITHHOLD


def test_contradiction_routes_to_review_not_publish() -> None:
    cond = GateConditions(**{**_all_pass(1.0).__dict__, "no_contradiction": False})
    decision = _decide(cond)
    assert decision.decision == REVIEW


def test_soft_failure_with_high_confidence_reviews_not_publishes() -> None:
    # Freshness failed but everything else (incl. confidence) is strong: an
    # official, evidenced offer that is not publishable is reviewed, not published.
    cond = GateConditions(**{**_all_pass(0.95).__dict__, "fresh": False})
    decision = _decide(cond)
    assert decision.decision == REVIEW
    assert "fresh" in decision.failed_conditions


def test_confidence_in_uncertain_band_reviews() -> None:
    assert _decide(_all_pass(0.80)).decision == REVIEW


def test_confidence_below_uncertain_is_withheld() -> None:
    assert _decide(_all_pass(0.50)).decision == WITHHOLD


def test_incomplete_schema_does_not_publish() -> None:
    cond = GateConditions(**{**_all_pass(0.95).__dict__, "schema_complete": False})
    assert _decide(cond).decision != PUBLISH


def test_invalid_nonempty_offer_type_makes_publication_conditions_incomplete(
    monkeypatch,
) -> None:
    facts = {
        "service": "Invalid Type Service",
        "offer_type": "FREE_FOREVER",
        "requires_card": False,
        "has_paid_dependencies": False,
        "exhaustion_behaviour": "hard_stop",
        "requests_per_day": "100/day",
    }
    candidate = SimpleNamespace(official=True, content_hash=_content_hash(facts), provider="test")
    source = SimpleNamespace(official=True, id=1, schedule="daily")
    revalidation = revalidate_quotas(facts, exhaustion_behaviour="hard_stop")
    monkeypatch.setattr("app.publish.publisher._pending_conflict_exists", lambda *a, **k: False)
    monkeypatch.setattr(
        "app.publish.publisher._freshest_fetched_at",
        lambda *a, **k: datetime.now(UTC),
    )

    conditions, _signals = _build_conditions(
        object(),
        candidate=candidate,
        source=source,
        facts=facts,
        revalidation=revalidation,
        evidence_backed=True,
        now=datetime.now(UTC),
    )
    decision = _decide(conditions)

    assert conditions.schema_complete is False
    assert decision.decision == REVIEW
    assert "schema_complete" in decision.failed_conditions


def test_non_deterministic_does_not_publish() -> None:
    cond = GateConditions(**{**_all_pass(0.95).__dict__, "deterministic": False})
    assert _decide(cond).decision != PUBLISH


def test_non_reproducible_does_not_publish() -> None:
    cond = GateConditions(**{**_all_pass(0.95).__dict__, "reproducible": False})
    assert _decide(cond).decision != PUBLISH


def test_decision_is_deterministic() -> None:
    cond = _all_pass(0.95)
    assert _decide(cond).decision == _decide(cond).decision
