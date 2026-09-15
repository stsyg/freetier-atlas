"""Proof tests for ``schedule_ref`` -> staleness-window resolution.

The defect being closed: ``config_sync`` wrote ``source.schedule_ref`` verbatim
into ``Source.schedule``; ``reconcile.parse_schedule_window`` cannot parse the
reference names (``official_pages`` / ``rss`` / ``mcp_documentation``), so every
source silently fell back to ``DEFAULT_STALENESS_WINDOW`` (7 days) -- 7x to 168x
more permissive than the declared cron cadence.

These tests pin the fix's four load-bearing properties:

* **derivation** -- a cron expression yields its nominal cadence, and the window
  is ``STALENESS_CADENCE_MULTIPLIER`` (2x) that cadence;
* **resolution** -- each declared ``schedule_ref`` resolves to the derived
  compact window, which round-trips through ``parse_schedule_window``;
* **fail-closed** -- an unresolvable reference is REJECTED (naming the ref), never
  defaulted to the 7-day window;
* **precision + the forbidden guard** -- named/compact windows are unaffected, and
  ``parse_schedule_window`` was NOT widened to accept the reference names (doing so
  would map a name onto an arbitrary window -- a guess, not a resolution).

The final test emits the mandatory impact measurement over the real committed
provider corpus.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.config.loader import load_and_validate
from app.config.models import ProviderConfig
from app.ingest.config_sync import (
    ScheduleResolutionError,
    _default_schedule_set,
    _desired_source_fields,
    resolve_schedule_window,
    resolve_source_windows,
)
from app.ingest.reconcile import (
    DEFAULT_STALENESS_WINDOW,
    STALENESS_CADENCE_MULTIPLIER,
    cron_to_cadence,
    derive_staleness_window,
    parse_schedule_window,
    window_to_compact,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROVIDERS_DIR = REPO_ROOT / "config" / "examples" / "providers"


# --------------------------------------------------------------------------- #
# Derivation: cron -> cadence -> window                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("cron", "cadence"),
    [
        ("* * * * *", timedelta(minutes=1)),
        ("17 * * * *", timedelta(hours=1)),  # rss
        ("23 */6 * * *", timedelta(hours=6)),  # structured_apis
        ("15 4 * * *", timedelta(days=1)),  # official_pages
        ("35 3 * * *", timedelta(days=1)),  # mcp_documentation
        ("0 5 * * 0", timedelta(days=7)),  # full_reconciliation (weekly, Sunday)
    ],
)
def test_cron_to_cadence_regular_schedules(cron: str, cadence: timedelta) -> None:
    assert cron_to_cadence(cron) == cadence


def test_cron_to_cadence_irregular_uses_tightest_gap() -> None:
    # Twice a day at 03:00 and 15:00 -> gaps of 12h and 12h; a source scanned
    # unevenly is held to its TIGHTEST cadence, the conservative choice.
    assert cron_to_cadence("0 3,15 * * *") == timedelta(hours=12)
    # 09:00 and 10:00 -> tightest gap is 1h (not the 23h wrap).
    assert cron_to_cadence("0 9,10 * * *") == timedelta(hours=1)


@pytest.mark.parametrize(
    "cron",
    [
        "",  # empty
        "15 4 * *",  # four fields
        "15 4 * * * *",  # six fields
        "60 4 * * *",  # minute out of range
        "15 24 * * *",  # hour out of range
        "15 4 * * MON",  # non-numeric grammar the schema forbids
        "*/0 * * * *",  # zero step
    ],
)
def test_cron_to_cadence_rejects_unsupported_grammar(cron: str) -> None:
    # A cron it cannot derive from returns None -- the fail-closed signal the
    # resolver turns into a rejection, never a silent default.
    assert cron_to_cadence(cron) is None


def test_derive_staleness_window_is_multiple_of_cadence() -> None:
    assert STALENESS_CADENCE_MULTIPLIER == 2
    assert derive_staleness_window(timedelta(days=1)) == timedelta(days=2)
    assert derive_staleness_window(timedelta(hours=1)) == timedelta(hours=2)
    assert derive_staleness_window(timedelta(hours=6)) == timedelta(hours=12)


@pytest.mark.parametrize(
    ("window", "compact"),
    [
        (timedelta(days=2), "2d"),
        (timedelta(hours=2), "2h"),
        (timedelta(hours=12), "12h"),
        (timedelta(weeks=2), "2w"),
        (timedelta(minutes=30), "30m"),
    ],
)
def test_window_to_compact_round_trips_through_parser(window: timedelta, compact: str) -> None:
    assert window_to_compact(window) == compact
    # The stored form must survive the downstream re-read unchanged; this is what
    # lets the resolved window ride the existing Source.schedule column.
    assert parse_schedule_window(compact) == window


# --------------------------------------------------------------------------- #
# Resolution: each declared ref -> derived compact window                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("official_pages", "2d"),  # daily cadence -> 2-day window (was silently 7d)
        ("rss", "2h"),  # hourly cadence -> 2-hour window (was silently 7d)
        ("mcp_documentation", "2d"),  # daily cadence -> 2-day window
        ("structured_apis", "12h"),  # 6-hourly cadence -> 12-hour window
    ],
)
def test_resolve_schedule_window_resolves_declared_refs(ref: str, expected: str) -> None:
    assert resolve_schedule_window(ref, _default_schedule_set()) == expected


def test_resolve_source_windows_over_real_cloudflare_config() -> None:
    model = load_and_validate(str(PROVIDERS_DIR / "cloudflare.example.yaml"))
    assert isinstance(model, ProviderConfig)
    windows = resolve_source_windows(model, _default_schedule_set())
    # The one rss source tightens 168x (7d -> 2h); the official_pages ones 3.5x.
    assert windows == {
        "cloudflare-workers-limits": "2d",
        "cloudflare-pages-limits": "2d",
        "cloudflare-pages-pricing": "2d",
        "cloudflare-changelog": "2h",
        "cloudflare-docs-mcp": "2d",
    }


def test_resolve_source_windows_defaults_to_canonical_schedule_set() -> None:
    # Omitting the ScheduleSet loads the canonical one (fail-closed if missing),
    # so callers that never pass schedules still resolve rather than store a name.
    model = load_and_validate(str(PROVIDERS_DIR / "cloudflare.example.yaml"))
    assert isinstance(model, ProviderConfig)
    assert resolve_source_windows(model) == resolve_source_windows(model, _default_schedule_set())


# --------------------------------------------------------------------------- #
# Fail-closed: an unresolvable ref is rejected, never defaulted                #
# --------------------------------------------------------------------------- #


def test_unknown_ref_is_rejected_naming_the_ref_not_defaulted() -> None:
    with pytest.raises(ScheduleResolutionError) as excinfo:
        resolve_schedule_window("no_such_schedule", _default_schedule_set())
    message = str(excinfo.value)
    assert "no_such_schedule" in message  # the offending ref is named
    assert "available cron schedules" in message  # and actionable
    # Fail-closed proof: it raised instead of returning the 7-day default. The
    # forbidden silent fallback would have returned DEFAULT_STALENESS_WINDOW.


def test_ref_naming_a_non_cron_entry_is_rejected() -> None:
    # ``conflict_recheck`` exists on the ScheduleSet but is an interval, not a
    # cron schedule; it cannot yield a cadence and must be rejected, not guessed.
    schedules = _default_schedule_set()
    if getattr(getattr(schedules, "conflict_recheck", None), "cron", None) is None:
        with pytest.raises(ScheduleResolutionError):
            resolve_schedule_window("conflict_recheck", schedules)


def test_ref_whose_cron_yields_no_cadence_is_rejected_not_defaulted() -> None:
    # A schedule entry whose cron the deriver cannot parse must fail closed. Real
    # ScheduleSet crons are schema-validated, so a stand-in carries the bad cron;
    # the assertion is that resolution RAISES rather than returning the 7-day
    # default -- the exact silent degradation this fix removes.
    broken = SimpleNamespace(rss=SimpleNamespace(cron="not a cron"))
    with pytest.raises(ScheduleResolutionError) as excinfo:
        resolve_schedule_window("rss", broken)
    assert "rss" in str(excinfo.value)
    assert cron_to_cadence("not a cron") is None  # the None signal it fails closed on


def test_resolve_source_windows_names_the_offending_source() -> None:
    sources = [
        SimpleNamespace(id="good-source", schedule_ref="official_pages"),
        SimpleNamespace(id="bad-source", schedule_ref="no_such_schedule"),
    ]
    config = SimpleNamespace(sources=sources)
    with pytest.raises(ScheduleResolutionError) as excinfo:
        resolve_source_windows(config, _default_schedule_set())  # type: ignore[arg-type]
    assert "bad-source" in str(excinfo.value)
    assert "no_such_schedule" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Precision + forbidden-approach guard                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("schedule", "window"),
    [
        ("hourly", timedelta(hours=1)),
        ("daily", timedelta(days=1)),
        ("weekly", timedelta(weeks=1)),
        ("monthly", timedelta(days=30)),
        ("2d", timedelta(days=2)),
        ("2h", timedelta(hours=2)),
        ("12h", timedelta(hours=12)),
    ],
)
def test_parse_schedule_window_named_and_compact_forms_unaffected(
    schedule: str, window: timedelta
) -> None:
    # The fix stores compact windows and leaves the named/compact grammar intact,
    # so a valid, resolvable window still parses exactly as before.
    assert parse_schedule_window(schedule) == window


@pytest.mark.parametrize("ref", ["official_pages", "rss", "mcp_documentation"])
def test_parse_schedule_window_was_not_widened_to_accept_ref_names(ref: str) -> None:
    # EXPLICITLY FORBIDDEN alternative: teaching parse_schedule_window the ref
    # names. That maps a name onto an arbitrary window -- a guess -- and leaves the
    # reference unresolved. It must still NOT recognise them: an unresolved name
    # reaching this function is exactly the defect, and it still degrades to the
    # default here, which is why resolution has to happen upstream at sync.
    assert parse_schedule_window(ref) == DEFAULT_STALENESS_WINDOW


# --------------------------------------------------------------------------- #
# Load-bearing: the column stores the resolved window, not the ref             #
# --------------------------------------------------------------------------- #


def test_source_schedule_column_stores_resolved_window_not_ref() -> None:
    # Load-bearing revert check: restoring the old bridge line
    #     "schedule": config.schedule_ref
    # (i.e. dropping the resolved ``schedule_window`` argument) makes this
    # assertion fail -- the column would carry "official_pages" again, which
    # parse_schedule_window silently maps to the 7-day default. An arity break is
    # not the point; this pins the SEMANTIC property that a resolved window,
    # not a reference name, reaches the database.
    fields = _desired_source_fields(
        SimpleNamespace(  # type: ignore[arg-type]
            id="cloudflare-workers-limits",
            type="html",
            trust_level="official",
            url="https://developers.cloudflare.com/workers/platform/limits/",
            schedule_ref="official_pages",
            extraction_profile="cloudflare_workers_limits",
        ),
        provider_id=1,
        schedule_window="2d",
    )
    assert fields["schedule"] == "2d"
    assert fields["schedule"] != "official_pages"


# --------------------------------------------------------------------------- #
# MANDATORY impact measurement over the real committed corpus                  #
# --------------------------------------------------------------------------- #


def test_impact_every_declared_source_window_is_strictly_tighter_than_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emit the before/after window table and assert the direction of change.

    Without a live database the exact per-offer verdict flip cannot be observed
    (integration currency tests are DATABASE_URL-gated and skip in this slice).
    What IS deterministic from the committed corpus is the window each source
    moves from (7d) to, and therefore the DIRECTION every possible flip can take:
    every derived window is strictly tighter than the old 7-day fallback, so the
    only reachable classification change is fresh -> stale (a withdrawal), and it
    only fires for persisted evidence whose age falls in ``(new_window, 7d]``.
    """

    schedules = _default_schedule_set()
    rows: list[tuple[str, str, str, str]] = []
    for path in sorted(PROVIDERS_DIR.glob("*.yaml")):
        model = load_and_validate(str(path))
        if not isinstance(model, ProviderConfig):
            continue
        windows = resolve_source_windows(model, schedules)
        for source in model.sources:
            new_compact = windows[source.id]
            new_window = parse_schedule_window(new_compact)
            assert new_window < DEFAULT_STALENESS_WINDOW, (
                f"{source.id}: derived window {new_compact} is not tighter than the "
                "7-day default; the fix must never widen a window"
            )
            rows.append((source.id, source.schedule_ref, "7d", new_compact))

    # The measured corpus shape from the contract: 36 declarations, 3 distinct
    # refs. Assert it so a corpus change that would alter the impact is caught.
    assert len(rows) == 36
    assert {r[1] for r in rows} == {"official_pages", "rss", "mcp_documentation"}

    header = f"{'source':38} {'ref':20} {'old':>5} -> {'new':>5}  flip-age-band"
    lines = [header, "-" * len(header)]
    for source_id, ref, old, new in rows:
        new_window = parse_schedule_window(new)
        band = f"({new}, 7d]"
        lines.append(f"{source_id:38} {ref:20} {old:>5} -> {new:>5}  {band}")
    print("\n=== schedule_ref resolution impact (window direction) ===")
    print("\n".join(lines))
    print(
        "\nDirection: every window tightens; only fresh->stale (withdrawal) is "
        "reachable, for evidence aged into the band shown. On a fresh scan "
        "(age ~= 0) nothing flips."
    )
    captured = capsys.readouterr()
    assert "fresh->stale" in captured.out
