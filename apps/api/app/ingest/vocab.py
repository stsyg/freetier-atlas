"""Closed vocabularies for the source-ingestion pipeline.

The single source of truth for the verification-state lifecycle that a candidate
fact moves through. It mirrors ``docs/ARCHITECTURE.md`` ("Verification states")
exactly so the ingestion contract and any future persistence agree.
"""

from __future__ import annotations

# docs/ARCHITECTURE.md -> Verification states.
#
# A source document is ``detected`` when first discovered, ``extracting`` while
# its material facts are parsed, ``candidate`` once parsed but not yet verified,
# then one of the terminal/steady states below. Adapters in this epic only ever
# produce candidate facts; nothing here can mark a fact ``verified``.
VERIFICATION_STATES: tuple[str, ...] = (
    "detected",
    "extracting",
    "candidate",
    "verified",
    "verified_with_caveats",
    "conflict",
    "stale",
    "withdrawn",
    "rejected",
)

# The states an adapter is permitted to assign to freshly extracted facts. A
# fact can never be born ``verified``; verification is a later, separate step.
ADAPTER_ASSIGNABLE_STATES: frozenset[str] = frozenset(
    {"detected", "extracting", "candidate", "rejected"}
)


def is_verification_state(value: str) -> bool:
    """True if ``value`` is a known verification state."""

    return value in VERIFICATION_STATES


__all__ = (
    "VERIFICATION_STATES",
    "ADAPTER_ASSIGNABLE_STATES",
    "is_verification_state",
)
