"""Closed vocabularies for the source-ingestion subsystem (F004).

Kept separate from :mod:`app.models.vocab` (the persisted-schema vocabularies)
because these describe the *in-flight* ingestion lifecycle rather than a database
column. They will back the ``candidate.verification_state`` check constraint when
the persistence slice (F004 slice 2) adds it, so they live in one place now to
avoid later drift.
"""

from __future__ import annotations

# docs/ARCHITECTURE.md -> "Verification states". The lifecycle a candidate fact
# moves through, from first detection to a terminal disposition. F004 adapters
# only ever produce *pre-publication* states; promotion to ``verified`` is a
# separate, deferred step (F005) and never happens inside an adapter.
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

# The verification states an F004 adapter is permitted to assign. Anything at or
# beyond ``verified`` requires the (deferred) verification/publication pipeline.
PRE_PUBLICATION_STATES: tuple[str, ...] = (
    "detected",
    "extracting",
    "candidate",
    "rejected",
)

# Source trust levels. ``official`` sources establish evidence; every other
# trust level may only ever produce unverified discovery candidates and can
# never create evidence (docs/SOURCE_REUSE_AND_PROVENANCE.md).
TRUST_OFFICIAL = "official"
TRUST_LEVELS: tuple[str, ...] = (
    TRUST_OFFICIAL,
    "community",
    "unknown",
)


def is_official(trust_level: str) -> bool:
    """True only for the ``official`` trust level (the sole evidence source)."""

    return trust_level == TRUST_OFFICIAL
