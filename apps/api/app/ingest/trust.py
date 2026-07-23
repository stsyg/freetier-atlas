"""Trust-level gating for the ingestion/quarantine separation boundary.

This module is the single, explicit place that expresses the hard product
invariant from ``docs/SOURCE_REUSE_AND_PROVENANCE.md``:

    Community repositories may *discover* candidates and expose coverage gaps.
    Only *official* providers establish facts.

Concretely, community/unverified sources may create only quarantined
``discovery_candidate`` rows (plus non-official ``candidate`` rows); they may
**never** produce ``evidence``, an official ``candidate``, an ``offer`` or an
``offer_version``. :func:`is_official_source` centralises the "is this source
trusted?" decision (previously an inline ``source.trust_level == "official"``
comparison inside :mod:`app.ingest.scan`) so the rule lives in exactly one place,
and :func:`assert_evidence_permitted` is a defense-in-depth guard that refuses to
attach evidence to a non-official/quarantined candidate.

This is the *application* half of a two-layer separation guarantee; the
*database* half (migration 0006) installs PL/pgSQL triggers that enforce the same
invariant even against raw SQL that bypasses this code path.

Pure and import-light: no database, network, or ORM import here, so the rule is
trivially unit-testable and cannot itself become a network/DB seam.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

#: The one ``source.trust_level`` value that is allowed to establish facts
#: (create official candidates and evidence). Everything else -- ``community``,
#: ``unverified``, or any other value -- is quarantine-only.
OFFICIAL_TRUST_LEVEL = "official"


@runtime_checkable
class _HasTrustLevel(Protocol):
    """Structural type for anything carrying a ``trust_level`` (e.g. a Source)."""

    trust_level: str


class SeparationError(RuntimeError):
    """Raised when code attempts to cross the community/official trust boundary.

    In practice this signals a programming error: a caller tried to attach
    ``evidence`` to a non-official (community/quarantined) candidate, which the
    quarantine invariant forbids. It is deliberately a loud exception rather than
    a silent skip so the violation surfaces in tests and logs.
    """


def is_official_source(source: _HasTrustLevel) -> bool:
    """Return whether ``source`` is trusted to establish facts.

    A source is official iff its ``trust_level`` is exactly
    :data:`OFFICIAL_TRUST_LEVEL`. Any other trust level (community, unverified,
    unknown, ...) is quarantine-only and can never create evidence.
    """

    return getattr(source, "trust_level", None) == OFFICIAL_TRUST_LEVEL


def assert_evidence_permitted(*, candidate_official: bool, trust_level: str | None = None) -> None:
    """Guard: refuse to create evidence for a non-official candidate.

    Called immediately before any ``evidence`` row is built. Evidence is
    official-provenance only; a community/quarantined candidate (``official``
    is False) must never receive it. ``trust_level`` is accepted for a clearer
    error message when the originating source is known.

    Raises :class:`SeparationError` when the invariant would be violated.
    """

    if not candidate_official:
        detail = f" (source trust_level={trust_level!r})" if trust_level is not None else ""
        raise SeparationError(
            "Refusing to create evidence for a non-official (community/quarantined) "
            f"candidate{detail}: community-sourced discovery can never become verified "
            "evidence. Only official sources establish facts "
            "(docs/SOURCE_REUSE_AND_PROVENANCE.md)."
        )


__all__: Sequence[str] = (
    "OFFICIAL_TRUST_LEVEL",
    "SeparationError",
    "is_official_source",
    "assert_evidence_permitted",
)
