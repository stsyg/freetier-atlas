"""Plain-language confidence labels for the read-only catalogue API (F005 slice 3).

Decision D039 (docs/DECISIONS.md): *simple public confidence labels; numeric
score in advanced evidence*. The catalogue therefore surfaces a plain-language
LABEL as the primary confidence field and only ever exposes the raw numeric
score inside an advanced/detail block.

The mapping is pure and deterministic. It reuses the publication gate thresholds
the S2 publisher persisted alongside the score in
``OfferVersion.material_facts['gate']`` (``automatic_threshold`` /
``uncertain_threshold``) so the label boundaries match the very thresholds that
decided publication. When those thresholds are absent, documented fixed
fallbacks are used. A missing score yields ``"unknown"`` -- never a guessed
label.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Fixed fallback thresholds, used only when a version did not persist its gate
#: thresholds. They mirror the shipped Cloudflare ``publishing`` config
#: (automatic 0.90 / uncertain 0.70).
DEFAULT_AUTOMATIC_THRESHOLD = 0.90
DEFAULT_UNCERTAIN_THRESHOLD = 0.70

#: The closed set of plain-language labels this module produces.
CONFIDENCE_LABELS: tuple[str, ...] = ("high", "medium", "low", "unknown")


def confidence_label(
    score: float | None,
    *,
    automatic_threshold: float | None = None,
    uncertain_threshold: float | None = None,
) -> str:
    """Map a numeric confidence ``score`` in ``[0, 1]`` onto a plain-language label.

    - ``score >= automatic_threshold`` -> ``"high"``
    - ``score >= uncertain_threshold`` -> ``"medium"``
    - ``score <  uncertain_threshold`` -> ``"low"``
    - ``score is None`` (or not a finite number) -> ``"unknown"`` (never guessed)

    ``automatic_threshold`` / ``uncertain_threshold`` default to the documented
    fixed fallbacks when not supplied (e.g. an older version without a persisted
    ``gate`` block).
    """

    if score is None:
        return "unknown"
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if value != value:  # NaN
        return "unknown"

    automatic = (
        automatic_threshold if automatic_threshold is not None else DEFAULT_AUTOMATIC_THRESHOLD
    )
    uncertain = (
        uncertain_threshold if uncertain_threshold is not None else DEFAULT_UNCERTAIN_THRESHOLD
    )
    # Guard against an inverted/degenerate threshold pair.
    if uncertain > automatic:
        uncertain = automatic

    if value >= automatic:
        return "high"
    if value >= uncertain:
        return "medium"
    return "low"


__all__: Sequence[str] = (
    "DEFAULT_AUTOMATIC_THRESHOLD",
    "DEFAULT_UNCERTAIN_THRESHOLD",
    "CONFIDENCE_LABELS",
    "confidence_label",
)
