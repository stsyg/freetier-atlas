"""A minimal reference JSON source adapter.

Makes the :class:`~app.ingest.base.SourceAdapter` contract concrete against a
structured (JSON) official source. It is intentionally small -- just enough to
demonstrate every contract method end-to-end through an injected
:class:`~app.ingest.fetch.Fetcher` (the :class:`~app.ingest.fetch.FixtureFetcher`
in tests) without any live network or database access.

The expected document shape is a JSON object::

    {
      "provider": "example",
      "offers": [
        {
          "service": "Widgets",
          "offer_type": "always_free",
          "requires_card": false,
          "has_paid_dependencies": false,
          "quotas": [{"metric": "requests", "exhaustion_behaviour": "hard_stop"}]
        }
      ]
    }

Each offer becomes one :class:`~app.ingest.base.CandidateFacts` (state
``candidate`` -- never ``verified``) with a JSON-pointer
:class:`~app.ingest.base.EvidenceLocation`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from app.ingest.base import (
    AdapterHealth,
    CandidateFacts,
    EvidenceLocation,
    SourceAdapter,
    SourceDocument,
)
from app.ingest.fetch import Fetcher, FetchError, FetchResult

_REQUIRED_OFFER_FIELDS = ("service", "offer_type")


class JsonOfferAdapter(SourceAdapter):
    """Reference adapter that parses offers from a JSON document."""

    name = "reference-json"

    def __init__(self, fetcher: Fetcher, source_urls: Sequence[str]) -> None:
        super().__init__(fetcher)
        self._source_urls = tuple(source_urls)

    def discover(self) -> Sequence[str]:
        return self._source_urls

    def fetch(self, url: str) -> FetchResult:
        # The sole network path -- delegated to the injected safe fetcher.
        return self.fetcher.fetch(url)

    def canonicalize(self, result: FetchResult) -> SourceDocument:
        # Re-serialise with sorted keys so cosmetic ordering never affects
        # extraction (deterministic canonical form).
        parsed = json.loads(result.content.decode("utf-8"))
        canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        return SourceDocument(
            url=result.final_url,
            mime=result.mime,
            content_hash=result.content_hash,
            fetched_at=result.fetched_at,
            raw=result.content,
            canonical=canonical,
        )

    def extract(self, document: SourceDocument) -> Sequence[CandidateFacts]:
        data = json.loads(document.canonical)
        provider = str(data.get("provider", "")) or "unknown"
        candidates: list[CandidateFacts] = []
        for index, offer in enumerate(data.get("offers", [])):
            facts = {
                "service": offer.get("service"),
                "offer_type": offer.get("offer_type"),
                "requires_card": offer.get("requires_card"),
                "has_paid_dependencies": offer.get("has_paid_dependencies"),
                "quotas": tuple(q.get("exhaustion_behaviour") for q in offer.get("quotas", [])),
            }
            candidate = CandidateFacts(
                provider=provider,
                source_url=document.url,
                facts=facts,
                evidence=(
                    EvidenceLocation(
                        url=document.url,
                        selector=f"$.offers[{index}]",
                        excerpt=json.dumps(offer, sort_keys=True)[:280],
                        content_hash=document.content_hash,
                    ),
                ),
                verification_state="candidate",
            )
            candidates.append(candidate)
        return candidates

    def validate(self, candidate: CandidateFacts) -> Sequence[str]:
        problems: list[str] = []
        for required in _REQUIRED_OFFER_FIELDS:
            if not candidate.facts.get(required):
                problems.append(f"Missing required field '{required}'.")
        if not candidate.evidence:
            problems.append("Candidate has no evidence location.")
        return problems

    def evidence(self, candidate: CandidateFacts) -> Sequence[EvidenceLocation]:
        return candidate.evidence

    def health(self) -> AdapterHealth:
        now = datetime.now(UTC)
        urls = self.discover()
        if not urls:
            return AdapterHealth(
                adapter=self.name,
                healthy=False,
                checked_at=now,
                detail="No source URLs configured.",
            )
        probe = urls[0]
        try:
            self.fetch(probe)
        except FetchError as exc:
            return AdapterHealth(
                adapter=self.name,
                healthy=False,
                checked_at=now,
                detail=f"{exc.reason}: {exc}",
                source_url=probe,
            )
        return AdapterHealth(
            adapter=self.name,
            healthy=True,
            checked_at=now,
            detail="Source reachable and parseable.",
            source_url=probe,
        )


__all__ = ("JsonOfferAdapter",)
