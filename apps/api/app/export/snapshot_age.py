"""Snapshot-age gating for the static catalogue export (F009-S2, deployment blocker).

The catalogue export (:mod:`app.export.catalogue`) freezes evidence currency at
**build time**: every claim is honest as of the injected ``as_of``. Nothing in the
artefact, however, stops a snapshot built on Monday from being served three weeks
later with every claim still reading "current as of Monday" -- the reader cannot
see that the *snapshot itself* has aged. That is the same PR #95 / #99 defect class
(an expired free claim shown as current) **displaced in time instead of across
surfaces**, and it is the direction a reader cannot see. ADR 0007 makes closing it
a hard precondition of publishing the export.

Why this needs no database, host, or network
=============================================
The inputs are already frozen in the artefact. Every serialised
``evidence_currency`` block (see :func:`app.read_api.service._currency_out`) carries,
per claim:

* ``checked`` -- was a currency check possible at all;
* ``oldest_fetched_at`` -- the absolute timestamp of the stalest backing evidence;
* ``window_days`` -- *that claim's own* refresh window (its stalest source's window,
  derived by the same :func:`app.ingest.reconcile.parse_schedule_window` the live
  currency path applies).

So this gate does **not** map claims back to sources, and does **not** re-open the
database. It re-applies the *identical* rule the currency path already uses --
``age > window`` (see :func:`app.ingest.reconcile.assess_staleness`) -- but with
``age = now - oldest_fetched_at`` (read time) instead of
``age = as_of - oldest_fetched_at`` (build time). Because each claim carries its own
window, per-source discrimination is automatic: a stale hourly-backed claim degrades
while a fresh daily-backed claim in the same snapshot does not. This is a *report of
the live mechanism at a later clock*, exactly as ``manifest.staleness_windows`` is a
report and not a second computation.

The correct rule is per-claim, not global
==========================================
A single global "tightest window" rule was explicitly rejected: the tightest window
in the real corpus is ``2h`` (the hourly ``rss`` source), so a global tightest rule
would present *nothing* as current two hours after the build -- a wrongly-withheld
free offer, which is a defect of equal severity to a wrongly-published one and in the
same direction a reader cannot see. A claim degrades only when **its own** backing
source has exceeded **its own** window.

There is exactly one honest *whole-snapshot* fact, and it is the **loosest** window,
not the tightest: once ``now - as_of`` exceeds the loosest per-source window, every
checked claim is necessarily degraded (each claim's window is <= the loosest source
window, and its total age exceeds ``now - as_of`` which exceeds that window). That is
reported as :attr:`SnapshotAgeReport.loosest_window_exceeded`; it is a coarse
"everything is provably stale" signal, not a separate correctness threshold, and it
never wrongly withholds -- below it, per-claim gating still passes fresh claims.

Purity
======
``now`` is injected and timezone-aware, exactly like the builder's ``as_of``. This
module reads **no** wall clock: the same inputs always yield the same report, so a
publish step and a browser applying it get identical verdicts. It emits **nothing**
into the artefact -- a ``now``-dependent verdict baked at build time would itself go
stale, which is the very bug this gate closes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from app.ingest.reconcile import parse_schedule_window

_NAIVE_NOW = "now must be timezone-aware; a snapshot-age gate's read clock cannot be naive"

#: The affirmative-free phrase the change feed emits only for a current claim
#: (:data:`app.export.feed.VERIFIED_FREE_PHRASE`). A degraded claim must never carry
#: it; kept here so the gate's own tests can assert its absence without importing the
#: feed's rendering path.
_UNVERIFIED_SENTENCE = (
    "Free-tier status unverified: backing evidence is stale or its currency is unknown."
)

#: Reason a claim is degraded at read time -- checked and current at build, now past
#: its own refresh window. Deliberately consistent with the feed's ``_free_summary``
#: wording (minus "at build time", because the whole point is that time has moved on).
_DEGRADED_REASON = (
    "Backing evidence is past its refresh window as of the read time, so this claim is "
    "no longer known to be current. " + _UNVERIFIED_SENTENCE
)

#: Reason a claim is unknown -- no checkable fetch time backs it. It was never current
#: and must not silently become fresh; it also must not be counted as degraded, which
#: would assert a check that never happened.
_UNKNOWN_REASON = (
    "No checkable fetch time backs this claim, so whether it is current cannot be "
    "established. " + _UNVERIFIED_SENTENCE
)


class Verdict(str, Enum):
    """A per-claim read-time currency verdict.

    ``str`` mixin so a verdict serialises to its own name in JSON without a custom
    encoder, and compares equal to that name in a test.
    """

    #: Checked, and still inside its own window at ``now``.
    CURRENT = "current"
    #: Checked and current at build, but past its own window at ``now``. Must not
    #: present as free.
    DEGRADED = "degraded"
    #: No checkable fetch time -- unknown at build, unknown now. Never free, never
    #: counted as a checked degradation.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ClaimVerdict:
    """One ``evidence_currency`` block re-judged at the injected ``now``."""

    #: The artefact the block was found in (e.g. ``catalogue/providers.json``).
    artefact: str
    #: A slash-delimited pointer to the block within that artefact's JSON.
    pointer: str
    verdict: Verdict
    #: A one-line explanation, or ``None`` when :attr:`Verdict.CURRENT`.
    reason: str | None
    oldest_fetched_at: datetime | None
    window_days: float | None


@dataclass(frozen=True, slots=True)
class SnapshotAgeReport:
    """The whole-snapshot verdict: per-claim judgements plus derived signals."""

    as_of: datetime | None
    now: datetime
    claims: tuple[ClaimVerdict, ...] = ()
    #: The loosest per-source window from ``manifest.staleness_windows``; ``None``
    #: when no manifest was supplied.
    loosest_window: timedelta | None = None

    @property
    def counts(self) -> dict[str, int]:
        """How many claims fell into each verdict (a positive-control surface)."""

        tally = {v.value: 0 for v in Verdict}
        for claim in self.claims:
            tally[claim.verdict.value] += 1
        tally["total"] = len(self.claims)
        return tally

    @property
    def loosest_window_exceeded(self) -> bool:
        """True when the snapshot is past its loosest window (provably all-dead).

        When this holds, every *checked* claim is necessarily :attr:`Verdict.DEGRADED`
        (see the module docstring's proof). It is a coarse whole-snapshot signal for a
        publisher's freshness policy, not a per-claim correctness gate.
        """

        if self.loosest_window is None or self.as_of is None:
            return False
        return (self.now - self.as_of) > self.loosest_window

    @property
    def any_current(self) -> bool:
        """True when at least one claim still presents as current at ``now``."""

        return any(c.verdict is Verdict.CURRENT for c in self.claims)

    @property
    def degraded(self) -> tuple[ClaimVerdict, ...]:
        """Every claim that was current at build but is past its window now."""

        return tuple(c for c in self.claims if c.verdict is Verdict.DEGRADED)


def _coerce_datetime(value: Any) -> datetime | None:
    """Read ``oldest_fetched_at`` whether it arrived as a datetime or ISO text.

    The in-memory artefact map already carries ``mode="json"`` values (ISO strings);
    a caller re-reading written JSON gets the same. Both parse to an aware datetime.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    raise TypeError(f"oldest_fetched_at must be datetime or ISO-8601 text, got {value!r}")


def evaluate_claim(
    *,
    checked: bool,
    oldest_fetched_at: Any,
    window_days: float | None,
    now: datetime,
) -> tuple[Verdict, str | None]:
    """Re-judge one claim's currency at ``now`` from its frozen inputs.

    Applies the same ``age > window`` inequality the live currency path uses, with
    ``age = now - oldest_fetched_at``. Fails closed to :attr:`Verdict.UNKNOWN` whenever
    a check is impossible (unchecked, or a missing timestamp/window), so "we could not
    look" can never read as fresh and "unknown stays unknown". ``now`` MUST be aware.
    """

    if now.tzinfo is None:
        raise ValueError(_NAIVE_NOW)

    fetched = _coerce_datetime(oldest_fetched_at)
    if not checked or fetched is None or window_days is None:
        return Verdict.UNKNOWN, _UNKNOWN_REASON

    age = now - fetched
    window = timedelta(days=float(window_days))
    if age > window:
        return Verdict.DEGRADED, _DEGRADED_REASON
    return Verdict.CURRENT, None


def _iter_currency_blocks(node: Any, pointer: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield every ``(pointer, evidence_currency dict)`` anywhere under ``node``.

    Recursive rather than an enumerated field list so a future serializer that adds an
    ``evidence_currency`` block on a new surface is gated automatically, with nothing to
    fall out of date.
    """

    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{pointer}/{key}"
            if key == "evidence_currency" and isinstance(value, dict):
                yield child, value
            else:
                yield from _iter_currency_blocks(value, child)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _iter_currency_blocks(item, f"{pointer}/{index}")


def _as_of_of(artefacts: dict[str, Any]) -> datetime | None:
    """The snapshot's ``as_of``, preferring the manifest, then any envelope."""

    manifest = artefacts.get("manifest.json")
    if isinstance(manifest, dict) and manifest.get("as_of"):
        return _coerce_datetime(manifest["as_of"])
    for envelope in artefacts.values():
        if isinstance(envelope, dict) and envelope.get("as_of"):
            return _coerce_datetime(envelope["as_of"])
    return None


def _loosest_window(artefacts: dict[str, Any]) -> timedelta | None:
    """The loosest window across ``manifest.staleness_windows``.

    Each window is re-derived from its compact form with the same
    :func:`parse_schedule_window` the currency path uses, so the gate and the manifest
    cannot disagree on what a window is. ``None`` when no manifest/windows are present.
    """

    manifest = artefacts.get("manifest.json")
    if not isinstance(manifest, dict):
        return None
    windows = manifest.get("staleness_windows")
    if not isinstance(windows, list) or not windows:
        return None
    parsed = [
        parse_schedule_window(entry.get("window")) for entry in windows if isinstance(entry, dict)
    ]
    return max(parsed) if parsed else None


def evaluate_export(artefacts: dict[str, Any], *, now: datetime) -> SnapshotAgeReport:
    """Judge a whole rendered export map at ``now`` (pure; no I/O, no wall clock).

    ``artefacts`` is the ``{relative_path: envelope}`` map
    :func:`app.export.catalogue.build_catalogue_export` returns (or the equivalent read
    back from disk). Every ``evidence_currency`` block in every envelope is re-judged;
    the manifest supplies ``as_of`` and the per-source windows. ``now`` MUST be aware,
    and a ``now`` before ``as_of`` is incoherent (clock skew) and raises.
    """

    if now.tzinfo is None:
        raise ValueError(_NAIVE_NOW)

    as_of = _as_of_of(artefacts)
    if as_of is not None and now < as_of:
        raise ValueError(
            "now is before the snapshot's as_of; a read clock cannot precede the build "
            f"clock (now={now.isoformat()}, as_of={as_of.isoformat()})"
        )

    claims: list[ClaimVerdict] = []
    for path in sorted(artefacts):
        for pointer, block in _iter_currency_blocks(artefacts[path]):
            window_days = block.get("window_days")
            oldest = block.get("oldest_fetched_at")
            verdict, reason = evaluate_claim(
                checked=bool(block.get("checked", False)),
                oldest_fetched_at=oldest,
                window_days=window_days,
                now=now,
            )
            claims.append(
                ClaimVerdict(
                    artefact=path,
                    pointer=pointer,
                    verdict=verdict,
                    reason=reason,
                    oldest_fetched_at=_coerce_datetime(oldest),
                    window_days=window_days,
                )
            )

    return SnapshotAgeReport(
        as_of=as_of,
        now=now,
        claims=tuple(claims),
        loosest_window=_loosest_window(artefacts),
    )


def evaluate_export_dir(out_dir: str | Path, *, now: datetime) -> SnapshotAgeReport:
    """Read-only convenience: load a written export directory and gate it at ``now``.

    Thin glue over :func:`evaluate_export` -- it loads ``manifest.json`` and every
    ``*.json`` envelope under ``out_dir`` and re-judges them. It carries **no**
    publish/no-publish policy; deciding what to do with a degraded snapshot belongs to
    the publication step (F009-S6), which supplies its own ``now``.
    """

    root = Path(out_dir)
    artefacts: dict[str, Any] = {}
    for file in sorted(root.rglob("*.json")):
        relative = file.relative_to(root).as_posix()
        artefacts[relative] = json.loads(file.read_text(encoding="utf-8"))
    return evaluate_export(artefacts, now=now)


__all__ = [
    "ClaimVerdict",
    "SnapshotAgeReport",
    "Verdict",
    "evaluate_claim",
    "evaluate_export",
    "evaluate_export_dir",
]
