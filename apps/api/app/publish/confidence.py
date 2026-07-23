"""Deterministic confidence + completeness/freshness scoring (F005 slice 2).

The publication gate needs a single, explainable, *deterministic* number that
summarises how well an official candidate is evidenced. This module computes it
purely (no I/O): given a set of boolean/continuous :class:`ConfidenceSignals` it
returns a fixed-precision score in ``[0, 1]``. The same signals always yield the
same score, and the score is stored verbatim in ``OfferVersion.material_facts``
(owner decision Q3: no schema migration).

Signals (docs/ARCHITECTURE.md "Publication gate", docs/DATA_MODEL.md
"Confidence"):

* ``official`` -- the backing source is a trusted official source;
* ``evidence_backed`` -- the candidate carries at least one official Evidence row;
* ``deterministic`` -- every material number re-validated (see
  :mod:`app.publish.revalidate`);
* ``reproducible`` -- re-hashing the facts reproduces the candidate's stored
  ``content_hash`` (an identical re-scan would yield identical facts);
* ``no_contradiction`` -- no *pending* cross-source contradiction for the
  identity;
* ``completeness`` -- fraction of the core material fields that are *known*;
* ``freshness`` -- how far inside its policy window the freshest evidence is.

The two continuous signals also expose the helpers used to derive them
(:func:`completeness` and :func:`freshness_ratio`) so the publisher and the gate
share one definition. Nothing here guesses a value: an unknown field lowers
completeness rather than being assumed present.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .revalidate import RevalidationResult

#: Core material fields whose *known-ness* defines schema completeness. These are
#: exactly the facts the Z0 engine needs (offer type, the two billing gates, the
#: exhaustion behaviour) plus the presence of at least one re-validated quota.
CORE_COMPLETENESS_FIELDS: tuple[str, ...] = (
    "service",
    "offer_type",
    "requires_card",
    "has_paid_dependencies",
    "exhaustion_behaviour",
    "quotas_present",
)

#: Deterministic weights (sum == 1.0). A fully-evidenced, complete, fresh,
#: uncontradicted official offer scores 1.0; each missing signal subtracts its
#: weight. Tuned so a genuine official Cloudflare offer clears the 0.90
#: ``automatic_threshold`` while any structural gap (unofficial, no evidence,
#: non-deterministic, contradicted) drops it well below it.
WEIGHTS: Mapping[str, float] = {
    "official": 0.25,
    "evidence_backed": 0.20,
    "deterministic": 0.15,
    "reproducible": 0.15,
    "no_contradiction": 0.10,
    "completeness": 0.10,
    "freshness": 0.05,
}

#: Fixed rounding precision so the persisted score is byte-stable across runs.
_PRECISION = 4


@dataclass(frozen=True)
class ConfidenceSignals:
    """The deterministic inputs to :func:`compute_confidence`."""

    official: bool
    evidence_backed: bool
    deterministic: bool
    reproducible: bool
    no_contradiction: bool
    completeness: float
    freshness: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "completeness", _clamp(self.completeness))
        object.__setattr__(self, "freshness", _clamp(self.freshness))


def _clamp(value: float) -> float:
    """Clamp a ratio into ``[0.0, 1.0]``."""

    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def completeness(
    facts: Mapping[str, Any], revalidation: RevalidationResult
) -> tuple[float, tuple[str, ...]]:
    """Fraction of :data:`CORE_COMPLETENESS_FIELDS` that are known.

    Returns ``(ratio, missing)``. A boolean gate field (``requires_card`` /
    ``has_paid_dependencies``) counts as known only when it is explicitly
    ``True``/``False`` (never ``None``); ``quotas_present`` is known when at least
    one quota re-validated to a real number.
    """

    missing: list[str] = []
    for name in CORE_COMPLETENESS_FIELDS:
        if name == "quotas_present":
            if revalidation.parsed_count <= 0:
                missing.append(name)
            continue
        value = facts.get(name)
        if name in ("requires_card", "has_paid_dependencies"):
            known = isinstance(value, bool)
        else:
            known = value is not None and str(value).strip() != ""
        if not known:
            missing.append(name)
    known_count = len(CORE_COMPLETENESS_FIELDS) - len(missing)
    ratio = known_count / len(CORE_COMPLETENESS_FIELDS)
    return ratio, tuple(missing)


def freshness_ratio(age: timedelta, window: timedelta) -> float:
    """Map an evidence age against its policy window onto ``[0, 1]``.

    Fresh (age 0) -> 1.0; exactly at the window boundary -> 0.0; stale -> 0.0. A
    non-positive window is treated as "no freshness credit" (0.0) rather than
    dividing by zero.
    """

    window_seconds = window.total_seconds()
    if window_seconds <= 0:
        return 0.0
    age_seconds = max(age.total_seconds(), 0.0)
    return _clamp(1.0 - age_seconds / window_seconds)


def compute_confidence(signals: ConfidenceSignals) -> float:
    """Weighted, deterministic confidence score in ``[0, 1]`` (4 dp)."""

    score = (
        WEIGHTS["official"] * (1.0 if signals.official else 0.0)
        + WEIGHTS["evidence_backed"] * (1.0 if signals.evidence_backed else 0.0)
        + WEIGHTS["deterministic"] * (1.0 if signals.deterministic else 0.0)
        + WEIGHTS["reproducible"] * (1.0 if signals.reproducible else 0.0)
        + WEIGHTS["no_contradiction"] * (1.0 if signals.no_contradiction else 0.0)
        + WEIGHTS["completeness"] * signals.completeness
        + WEIGHTS["freshness"] * signals.freshness
    )
    return round(_clamp(score), _PRECISION)


def signals_as_material_fact(signals: ConfidenceSignals) -> dict[str, Any]:
    """JSON-safe, order-stable snapshot of the signals for ``material_facts``."""

    return {
        "official": signals.official,
        "evidence_backed": signals.evidence_backed,
        "deterministic": signals.deterministic,
        "reproducible": signals.reproducible,
        "no_contradiction": signals.no_contradiction,
        "completeness": round(signals.completeness, _PRECISION),
        "freshness": round(signals.freshness, _PRECISION),
    }


__all__: Sequence[str] = (
    "CORE_COMPLETENESS_FIELDS",
    "WEIGHTS",
    "ConfidenceSignals",
    "completeness",
    "freshness_ratio",
    "compute_confidence",
    "signals_as_material_fact",
)
