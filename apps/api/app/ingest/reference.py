"""A minimal reference structured/JSON source adapter (F004 slice 1).

This adapter exists to make the :class:`~app.ingest.base.SourceAdapter` contract
concrete and testable end to end. It parses a small, well-defined JSON document
describing an offer's material facts, degrading missing fields to explicit
warnings rather than guessing values. Full per-provider adapters (RSS, HTML,
structured API, MCP) arrive in later F004 slices; this one is intentionally tiny.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from app.ingest.base import (
    AdapterHealth,
    CandidateFacts,
    EvidenceLocation,
    SourceAdapter,
    SourceDocument,
    utcnow,
)
from app.ingest.fetch import FetchResult

# The offer fact keys this reference adapter understands. Anything absent becomes
# a warning and is simply omitted from ``facts`` -- unknown, never guessed.
_KNOWN_FACT_KEYS: tuple[str, ...] = (
    "offer_type",
    "requires_card",
    "has_paid_dependencies",
    "exhaustion_behaviour",
)


class ReferenceJSONAdapter(SourceAdapter):
    """Reference adapter for a single official JSON facts document."""

    source_type = "structured"

    def __init__(
        self,
        *,
        source_id: str,
        trust_level: str,
        allowlist: Sequence[str],
        fetcher,  # noqa: ANN001 - Fetcher protocol, kept loose for injection
        document_url: str,
    ) -> None:
        super().__init__(
            source_id=source_id,
            trust_level=trust_level,
            allowlist=allowlist,
            fetcher=fetcher,
        )
        self._document_url = document_url
        self._last_success: object = None

    def discover(self) -> Sequence[str]:
        return (self._document_url,)

    def fetch(self, url: str) -> FetchResult:
        return self._fetcher.fetch(url)

    def canonicalize(self, result: FetchResult) -> SourceDocument:
        # Canonical form = the decoded body; the hash comes from the fetch layer
        # so identical upstream bytes yield an identical document hash.
        content = result.body.decode("utf-8", errors="replace")
        return SourceDocument(
            source_id=self.source_id,
            url=result.final_url,
            mime=result.mime,
            content=content,
            content_hash=result.content_hash,
            fetched_at=result.fetched_at,
            raw=result.body,
        )

    def extract(self, document: SourceDocument) -> CandidateFacts:
        warnings: list[str] = []
        facts: dict[str, object] = {}
        try:
            payload = json.loads(document.content)
        except json.JSONDecodeError as exc:
            # Malformed input is a handled outcome: reject, do not guess.
            return CandidateFacts(
                source_id=self.source_id,
                trust_level=self.trust_level,
                verification_state="rejected",
                facts={},
                content_hash=document.content_hash,
                warnings=(f"malformed JSON: {exc.msg}",),
            )
        if not isinstance(payload, dict):
            return CandidateFacts(
                source_id=self.source_id,
                trust_level=self.trust_level,
                verification_state="rejected",
                facts={},
                content_hash=document.content_hash,
                warnings=("document root is not a JSON object",),
            )
        for key in _KNOWN_FACT_KEYS:
            if key in payload:
                facts[key] = payload[key]
            else:
                warnings.append(f"missing fact {key!r}")
        return CandidateFacts(
            source_id=self.source_id,
            trust_level=self.trust_level,
            verification_state="candidate" if facts else "detected",
            facts=facts,
            content_hash=document.content_hash,
            warnings=tuple(warnings),
        )

    def validate(self, candidate: CandidateFacts) -> CandidateFacts:
        # Reference validation only checks obvious type sanity; unknown values are
        # dropped with a warning rather than coerced.
        cleaned: dict[str, object] = {}
        warnings = list(candidate.warnings)
        for key, value in candidate.facts.items():
            if key in ("requires_card", "has_paid_dependencies") and not isinstance(value, bool):
                warnings.append(f"fact {key!r} is not boolean; dropped")
                continue
            cleaned[key] = value
        return CandidateFacts(
            source_id=candidate.source_id,
            trust_level=candidate.trust_level,
            verification_state=candidate.verification_state,
            facts=cleaned,
            content_hash=candidate.content_hash,
            warnings=tuple(warnings),
        )

    def evidence(
        self, document: SourceDocument, candidate: CandidateFacts
    ) -> Sequence[EvidenceLocation]:
        # guard_evidence enforces the official-only rule structurally.
        if not candidate.facts:
            return self.guard_evidence(())
        locations = [
            EvidenceLocation(
                source_id=self.source_id,
                url=document.url,
                locator=f"$.{key}",
                excerpt=json.dumps({key: candidate.facts[key]}),
                content_hash=document.content_hash,
                captured_at=document.fetched_at,
            )
            for key in candidate.facts
        ]
        return self.guard_evidence(locations)

    def health(self) -> AdapterHealth:
        return AdapterHealth(
            source_id=self.source_id,
            ok=True,
            checked_at=utcnow(),
            detail="reference adapter ready",
        )


__all__ = ("ReferenceJSONAdapter",)
