"""Pure-logic unit tests for reconciliation (F004 Slice 3).

These exercise the deterministic, I/O-free core of
:mod:`app.ingest.reconcile` -- change classification, materiality, staleness, and
cross-source contradiction detection -- against the four acceptance scenarios
from the F004 contract: ``unchanged`` / ``changed`` / ``stale`` /
``contradictory``. No database is touched here; the live end-to-end path is
covered by ``tests/integration/test_ingest_reconcile.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.ingest.reconcile import (
    DEFAULT_STALENESS_WINDOW,
    ReconcileCandidate,
    assess_change,
    assess_staleness,
    changed_fields,
    classify_change_type,
    classify_materiality,
    counts_as_fresh_verification,
    find_contradictions,
    parse_schedule_window,
)

# A representative always-free candidate fact set.
_BASE_FACTS = {
    "service": "Widgets",
    "offer_type": "always_free",
    "requires_card": False,
    "has_paid_dependencies": False,
    "quotas": ["hard_stop"],
}


def _facts(**overrides: object) -> dict[str, object]:
    facts = dict(_BASE_FACTS)
    facts.update(overrides)
    return facts


# --- changed_fields --------------------------------------------------------


def test_changed_fields_detects_only_differences() -> None:
    assert changed_fields(_BASE_FACTS, _facts()) == ()
    assert changed_fields(_BASE_FACTS, _facts(requires_card=True)) == ("requires_card",)


def test_changed_fields_ignores_tuple_list_representation() -> None:
    # A fact parsed as a tuple (adapter) and re-read as a list (JSONB) are equal.
    tuple_facts = {"quotas": ("hard_stop", "throttled")}
    list_facts = {"quotas": ["hard_stop", "throttled"]}
    assert changed_fields(tuple_facts, list_facts) == ()


# --- classify_materiality --------------------------------------------------


def test_material_field_change_is_material() -> None:
    assert classify_materiality(["requires_card"]) == "material"
    assert classify_materiality(["quotas", "notes"]) == "material"


def test_only_cosmetic_change_is_non_material() -> None:
    assert classify_materiality(["display_name"]) == "non_material"
    assert classify_materiality([]) == "non_material"


def test_unrecognised_field_is_unknown_not_guessed() -> None:
    assert classify_materiality(["mystery_field"]) == "unknown"


# --- classify_change_type --------------------------------------------------


def test_change_type_added_when_new() -> None:
    assert (
        classify_change_type(
            seen_before=False, present_now=True, last_withdrawn=False, facts_changed=False
        )
        == "added"
    )


def test_change_type_modified_when_facts_changed() -> None:
    assert (
        classify_change_type(
            seen_before=True, present_now=True, last_withdrawn=False, facts_changed=True
        )
        == "modified"
    )


def test_change_type_none_when_unchanged() -> None:
    assert (
        classify_change_type(
            seen_before=True, present_now=True, last_withdrawn=False, facts_changed=False
        )
        is None
    )


def test_change_type_withdrawn_when_gone() -> None:
    assert (
        classify_change_type(
            seen_before=True, present_now=False, last_withdrawn=False, facts_changed=False
        )
        == "withdrawn"
    )


def test_change_type_none_when_already_withdrawn_and_absent() -> None:
    assert (
        classify_change_type(
            seen_before=True, present_now=False, last_withdrawn=True, facts_changed=False
        )
        is None
    )


def test_change_type_restored_after_withdrawal() -> None:
    assert (
        classify_change_type(
            seen_before=True, present_now=True, last_withdrawn=True, facts_changed=False
        )
        == "restored"
    )


# --- assess_change (unchanged / changed acceptance scenarios) --------------


def test_assess_change_unchanged_emits_nothing() -> None:
    assessment = assess_change(_BASE_FACTS, _facts())
    assert assessment.change_type is None


def test_assess_change_changed_is_single_material_modification() -> None:
    assessment = assess_change(_BASE_FACTS, _facts(requires_card=True))
    assert assessment.change_type == "modified"
    assert assessment.materiality == "material"
    assert assessment.changed_fields == ("requires_card",)


def test_assess_change_cosmetic_modification_is_non_material() -> None:
    assessment = assess_change(_BASE_FACTS, _facts(display_name="Widgets Pro"))
    assert assessment.change_type == "modified"
    assert assessment.materiality == "non_material"


def test_assess_change_added_is_material() -> None:
    assessment = assess_change(None, _BASE_FACTS, seen_before=False)
    assert assessment.change_type == "added"
    assert assessment.materiality == "material"


def test_assess_change_withdrawn_is_material() -> None:
    assessment = assess_change(_BASE_FACTS, None, seen_before=True)
    assert assessment.change_type == "withdrawn"
    assert assessment.materiality == "material"


def test_assess_change_restored_after_withdrawal() -> None:
    assessment = assess_change(_BASE_FACTS, _facts(), seen_before=True, last_withdrawn=True)
    assert assessment.change_type == "restored"
    assert assessment.materiality == "material"


# --- staleness (stale acceptance scenario) ---------------------------------


def test_parse_schedule_window_named_and_compact_and_default() -> None:
    assert parse_schedule_window("daily") == timedelta(days=1)
    assert parse_schedule_window("weekly") == timedelta(days=7)
    assert parse_schedule_window("6h") == timedelta(hours=6)
    assert parse_schedule_window("2w") == timedelta(weeks=2)
    # Unparseable / empty -> default fallback (never crashes).
    assert parse_schedule_window(None) == DEFAULT_STALENESS_WINDOW
    assert parse_schedule_window("whenever") == DEFAULT_STALENESS_WINDOW


def test_fresh_data_within_window_is_not_stale_and_counts_as_fresh() -> None:
    now = datetime(2026, 1, 8, tzinfo=UTC)
    fetched = now - timedelta(hours=12)
    staleness = assess_staleness(fetched, now, "daily")
    assert staleness.stale is False
    assert counts_as_fresh_verification(staleness) is True


def test_stale_data_beyond_window_is_flagged_and_not_a_fresh_verification() -> None:
    now = datetime(2026, 1, 8, tzinfo=UTC)
    fetched = now - timedelta(days=3)
    staleness = assess_staleness(fetched, now, "daily")
    assert staleness.stale is True
    # Stale data must NOT count as a fresh verification.
    assert counts_as_fresh_verification(staleness) is False


# --- contradictions (contradictory acceptance scenario) --------------------


def _view(ref: str, source_id: int, **facts: object) -> ReconcileCandidate:
    return ReconcileCandidate(
        ref=ref,
        source_id=source_id,
        identity=("example", "Widgets", "always_free"),
        facts=_facts(**facts),
        official=True,
    )


def test_no_contradiction_when_official_sources_agree() -> None:
    a = _view("a", 1)
    b = _view("b", 2)
    assert find_contradictions([a, b]) == []


def test_contradiction_raised_when_official_sources_disagree_on_material_fact() -> None:
    a = _view("a", 1, requires_card=False)
    b = _view("b", 2, requires_card=True)
    contradictions = find_contradictions([a, b])
    assert len(contradictions) == 1
    fields = {fc.field for fc in contradictions[0].conflicts}
    assert fields == {"requires_card"}


def test_same_source_difference_is_not_a_contradiction() -> None:
    # Same source over time is a change, never a cross-source contradiction.
    a = _view("a", 1, requires_card=False)
    b = _view("b", 1, requires_card=True)
    assert find_contradictions([a, b]) == []


def test_unknown_value_never_contradicts() -> None:
    a = _view("a", 1, requires_card=None)
    b = _view("b", 2, requires_card=True)
    assert find_contradictions([a, b]) == []


def test_non_material_disagreement_is_not_a_contradiction() -> None:
    a = _view("a", 1, display_name="Widgets")
    b = _view("b", 2, display_name="Widgets Pro")
    assert find_contradictions([a, b]) == []


def test_community_candidates_are_excluded_from_contradictions() -> None:
    a = ReconcileCandidate(
        ref="a",
        source_id=1,
        identity=("example", "Widgets", "always_free"),
        facts=_facts(requires_card=False),
        official=False,
    )
    b = _view("b", 2, requires_card=True)
    assert find_contradictions([a, b]) == []
