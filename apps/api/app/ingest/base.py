"""The source-adapter contract and its typed carriers.

Every provider integration is a :class:`SourceAdapter` implementing the seven
methods named in ``docs/ARCHITECTURE.md`` ("Source adapter contract"):

``discover`` -> ``fetch`` -> ``canonicalize`` -> ``extract`` -> ``validate`` ->
``evidence`` -> ``health``.

The contract is an :class:`abc.ABC`, so a subclass that forgets any one method
cannot be instantiated (``TypeError`` at construction time). Adapters return
:class:`SourceDocument` and :class:`CandidateFacts` -- **never** directly
published offers -- and reach the network *only* through an injected
:class:`~app.ingest.fetch.Fetcher`. No adapter imports an HTTP client directly;
the fetcher is the single network seam, which keeps the SSRF/allowlist guard
impossible to bypass.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.ingest.fetch import Fetcher, FetchResult
from app.ingest.vocab import ADAPTER_ASSIGNABLE_STATES, is_verification_state

# --- Typed carriers --------------------------------------------------------


@dataclass(frozen=True)
class SourceDocument:
    """A fetched, canonicalized source document ready for extraction.

    ``raw`` is the exact fetched bytes (for hashing/snapshotting); ``canonical``
    is the normalised textual form the extractor reads (e.g. re-serialised JSON,
    cleaned HTML text) so extraction is deterministic across cosmetic changes.
    """

    url: str
    mime: str
    content_hash: str
    fetched_at: datetime
    raw: bytes
    canonical: str


@dataclass(frozen=True)
class EvidenceLocation:
    """Where within a source a material claim was found (provenance)."""

    url: str
    selector: str | None = None
    excerpt: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class CandidateFacts:
    """Adapter-produced candidate facts about an offer.

    Candidate only -- never a published or verified fact. ``verification_state``
    must be one of :data:`~app.ingest.vocab.ADAPTER_ASSIGNABLE_STATES`; in
    particular an adapter can never mint a ``verified`` fact.
    """

    provider: str
    source_url: str
    facts: Mapping[str, Any]
    evidence: tuple[EvidenceLocation, ...] = ()
    verification_state: str = "candidate"

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", dict(self.facts))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not is_verification_state(self.verification_state):
            raise ValueError(f"Unknown verification state '{self.verification_state}'.")
        if self.verification_state not in ADAPTER_ASSIGNABLE_STATES:
            raise ValueError(
                f"An adapter may not assign verification state "
                f"'{self.verification_state}'; permitted: {sorted(ADAPTER_ASSIGNABLE_STATES)}."
            )


@dataclass(frozen=True)
class AdapterHealth:
    """The health of an adapter/source at a point in time."""

    adapter: str
    healthy: bool
    checked_at: datetime
    detail: str = ""
    source_url: str | None = None


# --- The contract ----------------------------------------------------------


class SourceAdapter(ABC):
    """Abstract base enforcing the seven-method source-adapter contract.

    Concrete adapters are constructed with a :class:`Fetcher` and reach the
    network only through it.
    """

    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher

    @property
    def fetcher(self) -> Fetcher:
        return self._fetcher

    @abstractmethod
    def discover(self) -> Sequence[str]:
        """Return the candidate source URLs this adapter knows how to fetch."""

    @abstractmethod
    def fetch(self, url: str) -> FetchResult:
        """Fetch one URL through the injected safe fetcher."""

    @abstractmethod
    def canonicalize(self, result: FetchResult) -> SourceDocument:
        """Normalise a raw fetch result into a deterministic source document."""

    @abstractmethod
    def extract(self, document: SourceDocument) -> Sequence[CandidateFacts]:
        """Parse candidate facts out of a canonicalized document."""

    @abstractmethod
    def validate(self, candidate: CandidateFacts) -> Sequence[str]:
        """Return a list of problems with a candidate (empty means valid)."""

    @abstractmethod
    def evidence(self, candidate: CandidateFacts) -> Sequence[EvidenceLocation]:
        """Return the evidence locations backing a candidate's material claims."""

    @abstractmethod
    def health(self) -> AdapterHealth:
        """Report whether the adapter/source is currently healthy."""


#: The seven method names the contract requires (used by contract tests/docs).
CONTRACT_METHODS: tuple[str, ...] = (
    "discover",
    "fetch",
    "canonicalize",
    "extract",
    "validate",
    "evidence",
    "health",
)


__all__ = (
    "SourceDocument",
    "EvidenceLocation",
    "CandidateFacts",
    "AdapterHealth",
    "SourceAdapter",
    "CONTRACT_METHODS",
)
