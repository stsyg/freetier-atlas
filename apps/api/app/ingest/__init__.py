"""Source-ingestion subsystem (F004).

Safe official-source adapters that produce reproducible candidates, evidence,
changes, health, and conflicts without publishing directly. The network boundary
lives entirely in :mod:`app.ingest.fetch`; the adapter contract lives in
:mod:`app.ingest.base`.
"""

from __future__ import annotations

from app.ingest.base import (
    AdapterHealth,
    CandidateFacts,
    EvidenceLocation,
    SourceAdapter,
    SourceDocument,
)
from app.ingest.fetch import (
    BlockedAddressError,
    DisallowedContentTypeError,
    DisallowedHostError,
    DisallowedSchemeError,
    Fetcher,
    FetchError,
    FetchPolicy,
    FetchResult,
    FetchTimeoutError,
    FixtureFetcher,
    FixtureResponse,
    NetworkDisabledError,
    OfflineFetcher,
    ResponseTooLargeError,
    SafeFetcher,
    TooManyRedirectsError,
    TransportError,
    default_fetcher,
)
from app.ingest.vocab import (
    PRE_PUBLICATION_STATES,
    TRUST_LEVELS,
    TRUST_OFFICIAL,
    VERIFICATION_STATES,
    is_official,
)

__all__ = (
    "AdapterHealth",
    "CandidateFacts",
    "EvidenceLocation",
    "SourceAdapter",
    "SourceDocument",
    "BlockedAddressError",
    "DisallowedContentTypeError",
    "DisallowedHostError",
    "DisallowedSchemeError",
    "FetchError",
    "Fetcher",
    "FetchPolicy",
    "FetchResult",
    "FetchTimeoutError",
    "FixtureFetcher",
    "FixtureResponse",
    "NetworkDisabledError",
    "OfflineFetcher",
    "ResponseTooLargeError",
    "SafeFetcher",
    "TooManyRedirectsError",
    "TransportError",
    "default_fetcher",
    "PRE_PUBLICATION_STATES",
    "TRUST_LEVELS",
    "TRUST_OFFICIAL",
    "VERIFICATION_STATES",
    "is_official",
)
