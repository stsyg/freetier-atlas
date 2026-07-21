"""Source-ingestion package.

The ingestion pipeline's foundation:

* :mod:`app.ingest.fetch` -- the safe fetch guard and the :class:`Fetcher` seam.
* :mod:`app.ingest.base` -- the :class:`SourceAdapter` contract and typed carriers.
* :mod:`app.ingest.vocab` -- the closed verification-state vocabulary.
* :mod:`app.ingest.reference` -- a minimal reference JSON adapter.
* :mod:`app.ingest.scan` -- ScanRun orchestration and candidate/evidence persistence.

Adapters produce *candidate* facts only; nothing here publishes or verifies.
"""

from __future__ import annotations

from .base import (
    AdapterHealth,
    CandidateFacts,
    EvidenceLocation,
    SourceAdapter,
    SourceDocument,
)
from .fetch import (
    BlockedAddressError,
    DisallowedHostError,
    DisallowedMimeError,
    DisallowedSchemeError,
    Fetcher,
    FetchError,
    FetchPolicy,
    FetchResult,
    FetchTimeoutError,
    FixtureFetcher,
    LiveFetcher,
    NetworkDisabledError,
    OfflineFetcher,
    ResponseTooLargeError,
    TooManyRedirectsError,
)
from .reference import JsonOfferAdapter
from .scan import ADAPTER_REGISTRY, UnknownAdapterError, build_adapter, run_scan
from .vocab import VERIFICATION_STATES, is_verification_state

__all__ = (
    # fetch
    "FetchPolicy",
    "FetchResult",
    "Fetcher",
    "OfflineFetcher",
    "LiveFetcher",
    "FixtureFetcher",
    "FetchError",
    "NetworkDisabledError",
    "DisallowedSchemeError",
    "DisallowedHostError",
    "BlockedAddressError",
    "DisallowedMimeError",
    "TooManyRedirectsError",
    "ResponseTooLargeError",
    "FetchTimeoutError",
    # contract
    "SourceAdapter",
    "SourceDocument",
    "CandidateFacts",
    "EvidenceLocation",
    "AdapterHealth",
    # reference adapter
    "JsonOfferAdapter",
    # scan orchestration
    "run_scan",
    "build_adapter",
    "ADAPTER_REGISTRY",
    "UnknownAdapterError",
    # vocab
    "VERIFICATION_STATES",
    "is_verification_state",
)
