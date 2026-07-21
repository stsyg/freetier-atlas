"""The source-adapter contract for the ingestion subsystem (F004 slice 1).

Every ingestion source -- structured API, RSS feed, static HTML document, MCP
tool -- is implemented as a :class:`SourceAdapter`. The adapter hides all
provider-specific logic behind a fixed seven-method contract
(docs/ARCHITECTURE.md "Source adapter contract"):

``discover`` -> ``fetch`` -> ``canonicalize`` -> ``extract`` -> ``validate``
-> ``evidence`` -> ``health``.

Two invariants are load-bearing for the product's anti-false-claim guarantee and
are enforced structurally rather than by convention:

* Adapters depend only on the injected :class:`~app.ingest.fetch.Fetcher`; they
  never import an HTTP client, so the network boundary stays a single hardened
  seam.
* Adapters emit :class:`CandidateFacts` in a *pre-publication* verification
  state only, and :meth:`SourceAdapter.evidence` yields evidence solely for
  ``official`` sources. Community/unknown sources can never produce evidence,
  so there is no adapter-level path to a verified or published offer.
"""

from __future__ import annotations

import abc
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.ingest.fetch import Fetcher, FetchResult
from app.ingest.vocab import (
    PRE_PUBLICATION_STATES,
    TRUST_LEVELS,
    is_official,
)


@dataclass(frozen=True)
class SourceDocument:
    """A fetched, canonicalized document ready for extraction.

    ``content`` is the canonical text form (e.g. decoded/normalised body) and
    ``content_hash`` is the stable hash of that canonical form, so two scans of
    identical upstream content produce identical hashes (reproducibility).
    """

    source_id: str
    url: str
    mime: str
    content: str
    content_hash: str
    fetched_at: datetime
    raw: bytes = b""


@dataclass(frozen=True)
class CandidateFacts:
    """Provisional facts extracted from a source document.

    Never a published offer. ``verification_state`` must be one of
    :data:`~app.ingest.vocab.PRE_PUBLICATION_STATES`; anything beyond that is the
    job of the (deferred) verification/publication pipeline, not an adapter.
    ``warnings`` records handled degradations (missing/partial data) so callers
    can distinguish "unknown" from "guessed".
    """

    source_id: str
    trust_level: str
    verification_state: str
    facts: Mapping[str, object]
    content_hash: str
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.trust_level not in TRUST_LEVELS:
            raise ValueError(f"trust_level {self.trust_level!r} not in {list(TRUST_LEVELS)}")
        if self.verification_state not in PRE_PUBLICATION_STATES:
            raise ValueError(
                f"verification_state {self.verification_state!r} is not a "
                f"pre-publication state {list(PRE_PUBLICATION_STATES)}; adapters "
                "may not assign verified/published states"
            )


@dataclass(frozen=True)
class EvidenceLocation:
    """A precise, re-checkable pointer to where a fact was found in a source.

    ``locator`` is a scheme-appropriate address (CSS selector, XPath, JSON
    pointer, feed entry id). ``excerpt`` is the supporting snippet and
    ``content_hash`` ties the evidence to a specific document revision.
    """

    source_id: str
    url: str
    locator: str
    excerpt: str
    content_hash: str
    captured_at: datetime


@dataclass(frozen=True)
class AdapterHealth:
    """A point-in-time health report for a source adapter."""

    source_id: str
    ok: bool
    checked_at: datetime
    detail: str = ""
    last_success_at: datetime | None = None


class SourceAdapter(abc.ABC):
    """Abstract base enforcing the seven-method source-ingestion contract.

    A concrete adapter is constructed with its immutable source configuration and
    an injected :class:`~app.ingest.fetch.Fetcher`. Instantiating a subclass that
    omits any contract method raises :class:`TypeError` at construction time.
    """

    #: The config ``type`` this adapter handles (subclasses must set it).
    source_type: str = ""

    def __init__(
        self,
        *,
        source_id: str,
        trust_level: str,
        allowlist: Sequence[str],
        fetcher: Fetcher,
    ) -> None:
        if trust_level not in TRUST_LEVELS:
            raise ValueError(f"trust_level {trust_level!r} not in {list(TRUST_LEVELS)}")
        self.source_id = source_id
        self.trust_level = trust_level
        self.allowlist = tuple(allowlist)
        self._fetcher = fetcher

    @property
    def is_official(self) -> bool:
        """Whether this source is trusted to establish evidence."""

        return is_official(self.trust_level)

    # --- the seven contract methods ------------------------------------- #
    @abc.abstractmethod
    def discover(self) -> Sequence[str]:
        """Return the candidate document URLs to fetch for this source."""

    @abc.abstractmethod
    def fetch(self, url: str) -> FetchResult:
        """Fetch one URL through the injected, screened fetcher."""

    @abc.abstractmethod
    def canonicalize(self, result: FetchResult) -> SourceDocument:
        """Normalise a fetch result into a stable, hashable document."""

    @abc.abstractmethod
    def extract(self, document: SourceDocument) -> CandidateFacts:
        """Extract candidate facts from a document (never a published offer)."""

    @abc.abstractmethod
    def validate(self, candidate: CandidateFacts) -> CandidateFacts:
        """Validate/normalise candidate facts; degrade to unknown, never guess."""

    @abc.abstractmethod
    def evidence(
        self, document: SourceDocument, candidate: CandidateFacts
    ) -> Sequence[EvidenceLocation]:
        """Return evidence locations. MUST be empty for non-official sources."""

    @abc.abstractmethod
    def health(self) -> AdapterHealth:
        """Report the adapter's current health."""

    # --- shared helpers -------------------------------------------------- #
    def guard_evidence(self, locations: Iterable[EvidenceLocation]) -> tuple[EvidenceLocation, ...]:
        """Enforce the community-vs-official separation at the contract level.

        Concrete ``evidence`` implementations should return
        ``self.guard_evidence(...)`` so a community/unknown source can never leak
        evidence even if a subclass forgets the rule.
        """

        materialised = tuple(locations)
        if materialised and not self.is_official:
            raise PermissionError(
                f"source {self.source_id!r} has trust_level {self.trust_level!r}; "
                "only official sources may produce evidence"
            )
        return materialised


def utcnow() -> datetime:
    """Timezone-aware current UTC timestamp (single definition for adapters)."""

    return datetime.now(UTC)


__all__ = (
    "SourceDocument",
    "CandidateFacts",
    "EvidenceLocation",
    "AdapterHealth",
    "SourceAdapter",
    "utcnow",
)
