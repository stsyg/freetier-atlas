"""The deterministic publication gate (F005 slice 2).

A candidate is published *only* when every hard condition holds and its
deterministic confidence clears the configured automatic threshold. Anything
short of that is never auto-published: contradictory or merely uncertain
evidence is routed to human review, and structurally ineligible input
(unofficial / unevidenced -- e.g. community data) is withheld outright. This
encodes owner decision Q4 and the gate list in docs/ARCHITECTURE.md
("Publication gate"):

    source official; schema-complete; deterministic; reproducible; backed by
    official evidence; no contradiction; confidence >= threshold; freshness
    within policy.

The module is pure: :func:`evaluate_gate` maps a :class:`GateConditions` plus the
two configured thresholds to a :class:`GateDecision` with an explainable reason
list, doing no I/O. The publisher owns turning a ``publish`` / ``review`` /
``withhold`` decision into database rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

#: The three terminal routes. ``publish`` is the only one that writes an
#: ``offer_version``; ``review`` raises a pending ``review_item``; ``withhold``
#: does nothing (the candidate stays quarantined / unevidenced).
PUBLISH = "publish"
REVIEW = "review"
WITHHOLD = "withhold"

#: The hard boolean conditions, in the order they are reported. Every one must
#: hold (together with confidence >= automatic threshold) for a publish.
HARD_CONDITIONS: tuple[str, ...] = (
    "official",
    "schema_complete",
    "deterministic",
    "reproducible",
    "evidence_backed",
    "no_contradiction",
    "fresh",
)


@dataclass(frozen=True)
class GateConditions:
    """The evaluated gate inputs for one candidate."""

    official: bool
    schema_complete: bool
    deterministic: bool
    reproducible: bool
    evidence_backed: bool
    no_contradiction: bool
    fresh: bool
    confidence: float

    def failed_hard(self) -> tuple[str, ...]:
        """Names of the hard boolean conditions that did not hold."""

        values = {
            "official": self.official,
            "schema_complete": self.schema_complete,
            "deterministic": self.deterministic,
            "reproducible": self.reproducible,
            "evidence_backed": self.evidence_backed,
            "no_contradiction": self.no_contradiction,
            "fresh": self.fresh,
        }
        return tuple(name for name in HARD_CONDITIONS if not values[name])


@dataclass(frozen=True)
class GateDecision:
    """The gate's verdict plus a human-readable rationale."""

    decision: str  # PUBLISH / REVIEW / WITHHOLD
    confidence: float
    automatic_threshold: float
    uncertain_threshold: float
    failed_conditions: tuple[str, ...] = ()
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def should_publish(self) -> bool:
        return self.decision == PUBLISH

    @property
    def needs_review(self) -> bool:
        return self.decision == REVIEW


def evaluate_gate(
    conditions: GateConditions,
    *,
    automatic_threshold: float,
    uncertain_threshold: float,
) -> GateDecision:
    """Route a candidate to publish / review / withhold, deterministically.

    Precedence (never auto-publishes anything that fails a hard condition):

    1. **Structurally ineligible** -- not official or not evidence-backed:
       withheld. Community/unofficial or unevidenced data can never be published
       and is never even offered for review-to-publish.
    2. **Contradiction** -- routed to human review (never auto-resolved).
    3. **Publish** -- every hard condition holds *and* confidence >= the
       automatic threshold.
    4. **Review** -- official + evidence-backed but some soft/hard condition
       failed or confidence sits in the uncertain band
       (``uncertain <= confidence < automatic``).
    5. **Withhold** -- confidence below the uncertain threshold.
    """

    failed = conditions.failed_hard()
    conf = conditions.confidence

    # 1. Structural gates: unofficial or unevidenced is withheld outright.
    if not conditions.official or not conditions.evidence_backed:
        reasons = []
        if not conditions.official:
            reasons.append("Source is not an official source; publication is not permitted.")
        if not conditions.evidence_backed:
            reasons.append("No official evidence backs the candidate; nothing to publish.")
        return GateDecision(
            decision=WITHHOLD,
            confidence=conf,
            automatic_threshold=automatic_threshold,
            uncertain_threshold=uncertain_threshold,
            failed_conditions=failed,
            reasons=tuple(reasons),
        )

    # 2. Contradiction: always human review, never auto-published/auto-resolved.
    if not conditions.no_contradiction:
        return GateDecision(
            decision=REVIEW,
            confidence=conf,
            automatic_threshold=automatic_threshold,
            uncertain_threshold=uncertain_threshold,
            failed_conditions=failed,
            reasons=(
                "Contradictory official evidence for this offer; routed to review, not published.",
            ),
        )

    # 3. Publish: all hard conditions hold and confidence clears the bar.
    if not failed and conf >= automatic_threshold:
        return GateDecision(
            decision=PUBLISH,
            confidence=conf,
            automatic_threshold=automatic_threshold,
            uncertain_threshold=uncertain_threshold,
            failed_conditions=(),
            reasons=(
                f"All gate conditions hold and confidence {conf:.4f} "
                f">= automatic threshold {automatic_threshold:.4f}.",
            ),
        )

    # 4/5. Official + evidenced but not publishable: review if uncertain, else
    # withhold when confidence is below the uncertain threshold.
    if conf >= uncertain_threshold:
        reasons = []
        if failed:
            reasons.append("Failed gate conditions require review: " + ", ".join(failed) + ".")
        if conf < automatic_threshold:
            reasons.append(
                f"Confidence {conf:.4f} is below the automatic threshold "
                f"{automatic_threshold:.4f}; routed to review."
            )
        return GateDecision(
            decision=REVIEW,
            confidence=conf,
            automatic_threshold=automatic_threshold,
            uncertain_threshold=uncertain_threshold,
            failed_conditions=failed,
            reasons=tuple(reasons),
        )

    return GateDecision(
        decision=WITHHOLD,
        confidence=conf,
        automatic_threshold=automatic_threshold,
        uncertain_threshold=uncertain_threshold,
        failed_conditions=failed,
        reasons=(
            f"Confidence {conf:.4f} is below the uncertain threshold "
            f"{uncertain_threshold:.4f}; withheld.",
        ),
    )


__all__: Sequence[str] = (
    "PUBLISH",
    "REVIEW",
    "WITHHOLD",
    "HARD_CONDITIONS",
    "GateConditions",
    "GateDecision",
    "evaluate_gate",
)
