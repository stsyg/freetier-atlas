"""Source-ingestion package.

The ingestion pipeline's foundation:

* :mod:`app.ingest.fetch` -- the safe fetch guard and the :class:`Fetcher` seam.
* :mod:`app.ingest.base` -- the :class:`SourceAdapter` contract and typed carriers.
* :mod:`app.ingest.vocab` -- the closed verification-state vocabulary.
* :mod:`app.ingest.reference` -- a minimal reference JSON adapter.
* :mod:`app.ingest.scan` -- ScanRun orchestration and candidate/evidence persistence.
* :mod:`app.ingest.reconcile` -- change/staleness/contradiction reconciliation.

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
from .reconcile import (
    DEFAULT_STALENESS_WINDOW,
    MATERIAL_FACT_FIELDS,
    NON_MATERIAL_FACT_FIELDS,
    ChangeAssessment,
    Contradiction,
    FieldConflict,
    ReconcileCandidate,
    ReconcileResult,
    StalenessAssessment,
    assess_change,
    assess_staleness,
    changed_fields,
    classify_change_type,
    classify_materiality,
    counts_as_fresh_verification,
    find_contradictions,
    parse_schedule_window,
    reconcile_scan,
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
    # reconciliation
    "reconcile_scan",
    "ReconcileResult",
    "ChangeAssessment",
    "StalenessAssessment",
    "ReconcileCandidate",
    "FieldConflict",
    "Contradiction",
    "MATERIAL_FACT_FIELDS",
    "NON_MATERIAL_FACT_FIELDS",
    "DEFAULT_STALENESS_WINDOW",
    "changed_fields",
    "classify_materiality",
    "classify_change_type",
    "assess_change",
    "parse_schedule_window",
    "assess_staleness",
    "counts_as_fresh_verification",
    "find_contradictions",
    # vocab
    "VERIFICATION_STATES",
    "is_verification_state",
)
