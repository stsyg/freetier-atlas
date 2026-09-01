"""The publisher's freshness wiring, including the branch with no snapshot at all.

WHY THIS FILE EXISTS
--------------------
``app.publish.publisher._build_conditions`` turns a source's freshest snapshot
into the gate's ``fresh`` hard condition:

.. code-block:: python

    freshest = _freshest_fetched_at(session, source_id=source.id)
    if freshest is None:
        fresh = False
        fresh_ratio = 0.0
    else:
        staleness = assess_staleness(freshest, now, source.schedule)
        fresh = not staleness.stale

Measured at 6e14a471 by mutation over the whole tree (3006 tests, green first):

* hardcoding ``fresh = True`` in the ``else`` arm was KILLED by 8 tests, seven of
  them the provider ``stale_case_never_publishes`` integration cases -- the
  runtime staleness gate is genuinely reached;
* flipping the ``freshest is None`` arm to ``fresh = True`` -- so a source that
  has produced **no snapshot whatsoever** is reported as fresh -- left all 3006
  GREEN.

The fail-closed default was therefore the one part of this wiring nothing
asserted, and it is the more dangerous half: "no evidence of freshness" being
read as "fresh" is exactly the unknown-treated-as-verified failure this product
forbids. Nothing here changes it; this pins it.

Every case is paired with its opposite arm, so none of them would pass against a
publisher that simply reported ``fresh=False`` for everything -- which would
withhold the entire catalogue and is no more acceptable than the hole.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from app.ingest.scan import _content_hash
from app.publish.gate import PUBLISH, evaluate_gate
from app.publish.publisher import _build_conditions
from app.publish.revalidate import revalidate_quotas

AUTO = 0.90
UNCERTAIN = 0.70
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

FACTS: dict[str, Any] = {
    "service": "Widgets",
    "offer_type": "always_free",
    "requires_card": False,
    "has_paid_dependencies": False,
    "exhaustion_behaviour": "hard_stop",
    "requests_per_day": "100/day",
}


def _conditions(monkeypatch: pytest.MonkeyPatch, *, freshest: datetime | None):
    """Drive the real ``_build_conditions`` with one controlled snapshot time.

    Only the two database reads are stubbed. Everything the freshness decision
    depends on -- ``assess_staleness``, the schedule window, the gate itself --
    is the production code path.
    """

    candidate = SimpleNamespace(official=True, content_hash=_content_hash(FACTS), provider="test")
    source = SimpleNamespace(official=True, id=1, schedule="daily")
    revalidation = revalidate_quotas(FACTS, exhaustion_behaviour="hard_stop")

    monkeypatch.setattr("app.publish.publisher._pending_conflict_exists", lambda *a, **k: False)
    monkeypatch.setattr("app.publish.publisher._freshest_fetched_at", lambda *a, **k: freshest)

    return _build_conditions(
        object(),
        candidate=candidate,
        source=source,
        facts=FACTS,
        revalidation=revalidation,
        evidence_backed=True,
        now=NOW,
    )


def _decide(conditions):
    return evaluate_gate(conditions, automatic_threshold=AUTO, uncertain_threshold=UNCERTAIN)


def test_a_source_with_no_snapshot_at_all_is_not_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absence of evidence is not evidence of freshness."""

    conditions, signals = _conditions(monkeypatch, freshest=None)

    assert conditions.fresh is False
    assert signals.freshness == 0.0
    decision = _decide(conditions)
    assert decision.decision != PUBLISH
    assert "fresh" in decision.failed_conditions


def test_a_snapshot_inside_the_schedule_window_is_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """The arm that stops the case above being true of a publisher that withholds
    everything. Same inputs, same stubs, one differing value: the snapshot exists."""

    conditions, signals = _conditions(monkeypatch, freshest=NOW - timedelta(hours=1))

    assert conditions.fresh is True
    assert signals.freshness > 0.0
    decision = _decide(conditions)
    assert "fresh" not in decision.failed_conditions
    assert decision.decision == PUBLISH


def test_a_snapshot_beyond_the_schedule_window_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``daily`` source whose freshest snapshot is a week old."""

    conditions, _signals = _conditions(monkeypatch, freshest=NOW - timedelta(days=7))

    assert conditions.fresh is False
    decision = _decide(conditions)
    assert decision.decision != PUBLISH
    assert "fresh" in decision.failed_conditions


def test_no_snapshot_and_a_stale_snapshot_reach_the_same_verdict_by_different_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both non-fresh, but only one of them scores any freshness at all.

    Without this the two branches would be indistinguishable, and a refactor that
    collapsed the ``None`` case into the staleness call -- computing an age
    against a missing timestamp -- would look identical from outside.
    """

    absent, absent_signals = _conditions(monkeypatch, freshest=None)
    stale, stale_signals = _conditions(monkeypatch, freshest=NOW - timedelta(days=7))

    assert absent.fresh is stale.fresh is False
    assert absent_signals.freshness == 0.0
    # A stale-but-present snapshot still carries a real, computed ratio; the
    # absent case is a hard zero rather than the same number by coincidence.
    assert stale_signals.freshness >= 0.0
    assert (absent_signals.freshness, stale_signals.freshness) == (
        0.0,
        stale_signals.freshness,
    )
