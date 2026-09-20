"""DB-free unit tests for snapshot-age gating (F009-S2, deployment blocker).

These pin the five acceptance invariants of :mod:`app.export.snapshot_age`:

1. a claim past its **own** window degrades (subject mutation) and no longer
   presents as free;
2. a claim **within** its window still presents normally (the positive control
   that guards the wrongful-withholding direction);
3. **per-source discrimination** -- an hourly-backed claim past its window and a
   daily-backed claim within it, at one shared ``now``, are judged differently (a
   global rule would fail this);
4. **unknown stays unknown** -- a claim with no checkable fetch time never becomes
   fresh, paired with a positive control so ``UNKNOWN`` is not a stuck instrument;
5. **purity** -- ``now`` is injected, the same inputs give the same report, a naive
   or pre-``as_of`` ``now`` is refused, and the module reads no wall clock.

Every ``evidence_currency`` block is produced through the *real* serialization path
(:func:`app.read_api.service._currency_out` over
:func:`app.read_api.currency.assess_currency`), so the gate is tested against the
exact bytes the export emits -- never a hand-rolled double.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.export import snapshot_age
from app.export.catalogue import _envelope
from app.export.feed import VERIFIED_FREE_PHRASE
from app.export.snapshot_age import (
    Verdict,
    evaluate_claim,
    evaluate_export,
    evaluate_export_dir,
)
from app.read_api import service
from app.read_api.currency import UNCHECKED, assess_currency

_AS_OF = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def _currency_block(*, fetched_at: datetime | None, schedule: str) -> dict:
    """A serialized ``evidence_currency`` block frozen at ``_AS_OF``.

    ``fetched_at=None`` yields the unchecked (declaration-only) shape; anything else
    is assessed against ``schedule`` exactly as a live handler would at build time.
    """

    verdict = UNCHECKED if fetched_at is None else assess_currency(fetched_at, _AS_OF, schedule)
    return service._currency_out(verdict).model_dump(mode="json")


def _offer_envelope(offer_id: int, block: dict) -> dict:
    """Wrap one currency block in a plausible offer-detail envelope."""

    data = {"offer_id": offer_id, "zero_cost_class": "Z0_TRUE_FREE", "evidence_currency": block}
    return _envelope(_AS_OF, f"/catalogue/offers/{offer_id}", data)


def _manifest(windows: list[str]) -> dict:
    return {
        "as_of": _AS_OF.astimezone(UTC).isoformat(),
        "staleness_windows": [{"source": f"s{i}", "window": w} for i, w in enumerate(windows)],
    }


# --------------------------------------------------------------------------- #
# Invariant 1 + 2: degrade past own window, positive control within it        #
# --------------------------------------------------------------------------- #


def test_claim_past_its_own_window_degrades_by_subject_mutation() -> None:
    # Fetched one day before the build, on a 2-day window: current at as_of.
    block = _currency_block(fetched_at=_AS_OF - timedelta(days=1), schedule="2d")
    assert block["current"] is True  # honest at build time

    # Read three days after the build -> total age 4 days > 2-day window.
    now = _AS_OF + timedelta(days=3)
    verdict, reason = evaluate_claim(
        checked=block["checked"],
        oldest_fetched_at=block["oldest_fetched_at"],
        window_days=block["window_days"],
        now=now,
    )
    assert verdict is Verdict.DEGRADED
    # It must not present as free: the affirmative-free phrase never appears.
    assert VERIFIED_FREE_PHRASE not in (reason or "")
    assert "unverified" in (reason or "")


def test_claim_within_its_window_stays_current_positive_control() -> None:
    # Same claim, read only twelve hours after the build -> age 1.5 days <= 2 days.
    block = _currency_block(fetched_at=_AS_OF - timedelta(days=1), schedule="2d")
    now = _AS_OF + timedelta(hours=12)
    verdict, reason = evaluate_claim(
        checked=block["checked"],
        oldest_fetched_at=block["oldest_fetched_at"],
        window_days=block["window_days"],
        now=now,
    )
    assert verdict is Verdict.CURRENT
    assert reason is None


# --------------------------------------------------------------------------- #
# Invariant 3: per-source discrimination (a global rule fails this)           #
# --------------------------------------------------------------------------- #


def test_hourly_and_daily_claims_are_judged_independently() -> None:
    hourly = _offer_envelope(
        1, _currency_block(fetched_at=_AS_OF - timedelta(hours=1), schedule="2h")
    )
    daily = _offer_envelope(
        2, _currency_block(fetched_at=_AS_OF - timedelta(days=1), schedule="2d")
    )
    artefacts = {
        "manifest.json": _manifest(["2h", "2d"]),
        "catalogue/offers/1.json": hourly,
        "catalogue/offers/2.json": daily,
    }

    # Three hours after the build: hourly age 4h > 2h (dead); daily age 27h < 48h (alive).
    report = evaluate_export(artefacts, now=_AS_OF + timedelta(hours=3))
    by_artefact = {c.artefact: c.verdict for c in report.claims}
    assert by_artefact["catalogue/offers/1.json"] is Verdict.DEGRADED
    assert by_artefact["catalogue/offers/2.json"] is Verdict.CURRENT
    # Proven different -- the whole reason the rule is per-claim, not global.
    assert by_artefact["catalogue/offers/1.json"] is not by_artefact["catalogue/offers/2.json"]
    assert report.counts == {"current": 1, "degraded": 1, "unknown": 0, "total": 2}


# --------------------------------------------------------------------------- #
# Invariant 4: unknown stays unknown (with a positive control)                #
# --------------------------------------------------------------------------- #


def test_unchecked_claim_never_becomes_fresh() -> None:
    block = _currency_block(fetched_at=None, schedule="2d")
    assert block["checked"] is False
    # At any read time -- even the instant of the build -- it stays unknown.
    for now in (_AS_OF, _AS_OF + timedelta(days=365)):
        verdict, reason = evaluate_claim(
            checked=block["checked"],
            oldest_fetched_at=block["oldest_fetched_at"],
            window_days=block["window_days"],
            now=now,
        )
        assert verdict is Verdict.UNKNOWN
        assert VERIFIED_FREE_PHRASE not in (reason or "")


def test_unknown_is_not_a_stuck_instrument() -> None:
    # The positive control: a checked, in-window claim through the same evaluator
    # reads CURRENT, so the UNKNOWN above cannot be an evaluator stuck at one value.
    block = _currency_block(fetched_at=_AS_OF - timedelta(hours=1), schedule="2d")
    verdict, _ = evaluate_claim(
        checked=block["checked"],
        oldest_fetched_at=block["oldest_fetched_at"],
        window_days=block["window_days"],
        now=_AS_OF,
    )
    assert verdict is Verdict.CURRENT


# --------------------------------------------------------------------------- #
# Whole-snapshot loosest-window fact (a different question, not the tightest)  #
# --------------------------------------------------------------------------- #


def test_loosest_window_governs_the_all_dead_signal_not_the_tightest() -> None:
    # Two claims both fresh at build; windows 2h (tight) and 2d (loose).
    artefacts = {
        "manifest.json": _manifest(["2h", "2d"]),
        "catalogue/offers/1.json": _offer_envelope(
            1, _currency_block(fetched_at=_AS_OF, schedule="2h")
        ),
        "catalogue/offers/2.json": _offer_envelope(
            2, _currency_block(fetched_at=_AS_OF, schedule="2d")
        ),
    }

    # Past the TIGHT window but inside the LOOSE one: not all-dead, and the daily
    # claim survives -- the corrected rule must NOT withhold it.
    inside = evaluate_export(artefacts, now=_AS_OF + timedelta(hours=6))
    assert inside.loosest_window == timedelta(days=2)
    assert inside.loosest_window_exceeded is False
    assert inside.any_current is True

    # Past the LOOSE window: provably all-dead and no survivors.
    beyond = evaluate_export(artefacts, now=_AS_OF + timedelta(days=3))
    assert beyond.loosest_window_exceeded is True
    assert beyond.any_current is False
    assert all(c.verdict is Verdict.DEGRADED for c in beyond.claims)


# --------------------------------------------------------------------------- #
# Invariant 5: purity                                                         #
# --------------------------------------------------------------------------- #


def test_naive_now_is_refused() -> None:
    naive_now = datetime(2026, 1, 16, 12, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_claim(checked=True, oldest_fetched_at=_AS_OF, window_days=1.0, now=naive_now)


def test_now_before_as_of_is_incoherent() -> None:
    artefacts = {"manifest.json": _manifest(["2d"])}
    with pytest.raises(ValueError, match="before the snapshot's as_of"):
        evaluate_export(artefacts, now=_AS_OF - timedelta(hours=1))


def test_same_inputs_give_the_same_report() -> None:
    artefacts = {
        "manifest.json": _manifest(["2d"]),
        "catalogue/offers/1.json": _offer_envelope(
            1, _currency_block(fetched_at=_AS_OF - timedelta(days=1), schedule="2d")
        ),
    }
    now = _AS_OF + timedelta(days=3)
    assert evaluate_export(artefacts, now=now).claims == evaluate_export(artefacts, now=now).claims


def test_module_reads_no_wall_clock() -> None:
    # The gate must never consult the wall clock; ``now`` is injected. Guarding by
    # source inspection catches a regression an output check could miss.
    source = Path(snapshot_age.__file__).read_text(encoding="utf-8")
    assert not re.search(r"datetime\.now\(", source)
    assert not re.search(r"datetime\.utcnow\(", source)


# --------------------------------------------------------------------------- #
# The read-only directory loader is faithful glue over the pure core          #
# --------------------------------------------------------------------------- #


def test_evaluate_export_dir_matches_in_memory_evaluation(tmp_path: Path) -> None:
    import json

    artefacts = {
        "manifest.json": _manifest(["2h", "2d"]),
        "catalogue/offers/1.json": _offer_envelope(
            1, _currency_block(fetched_at=_AS_OF - timedelta(hours=1), schedule="2h")
        ),
        "catalogue/offers/2.json": _offer_envelope(
            2, _currency_block(fetched_at=_AS_OF - timedelta(days=1), schedule="2d")
        ),
    }
    for rel, obj in artefacts.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(obj), encoding="utf-8")

    now = _AS_OF + timedelta(hours=3)
    from_dir = {(c.artefact, c.verdict) for c in evaluate_export_dir(tmp_path, now=now).claims}
    in_memory = {(c.artefact, c.verdict) for c in evaluate_export(artefacts, now=now).claims}
    assert from_dir == in_memory
