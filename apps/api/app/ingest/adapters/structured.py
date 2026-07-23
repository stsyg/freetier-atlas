"""Structured-API (JSON / REST) source adapter (standard library only).

Parses an official structured endpoint -- a JSON offer or pricing API response --
into *candidate* facts using stdlib :mod:`json`. The adapter itself contains
**no** provider-specific keys: it is a generic record-walking engine driven by a
declarative :class:`~app.ingest.adapters._json.JsonExtractionProfile`. All
provider/document-specific knowledge (which array holds the records, and how its
keys map to offer facts) lives in the :data:`JSON_EXTRACTION_PROFILES` registry
-- i.e. in configuration, not code.

Design contract (docs/AGENT_HARNESS.md "unknown is better than guessed"):

* The network is reached only through the injected
  :class:`~app.ingest.fetch.Fetcher`; no HTTP client is imported, so a
  non-allowlisted URL is refused by the shared safe fetcher pre-connection.
* Each record under the profile's ``records_path`` becomes one candidate. A key
  that is absent, or a value that is ambiguous, yields ``None`` (UNKNOWN) -- never
  a guessed value.
* Malformed input never crashes and never fabricates a value: invalid JSON, a
  non-mapping root, or a records path that is absent / not a list yields a single
  ``rejected`` candidate whose only facts are ``{error, detail}``, which
  :meth:`validate` flags (a *captured validation failure*). A partial record
  simply carries ``None`` for its missing fields.

An offer endpoint is expected to look like::

    {
      "provider": "example",
      "offers": [
        {"service": "Object Store", "billing": "always_free",
         "card_required": false, "paid_addons": false,
         "quotas": ["storage=hard_stop", "egress=throttle"]}
      ]
    }
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from app.ingest.adapters._common import host
from app.ingest.adapters._json import (
    JsonExtractionProfile,
    JsonField,
    extract_records,
)
from app.ingest.base import (
    AdapterHealth,
    CandidateFacts,
    EvidenceLocation,
    SourceAdapter,
    SourceDocument,
)
from app.ingest.fetch import Fetcher, FetchError, FetchResult

_EXCERPT_LIMIT = 280


class UnknownJsonProfileError(ValueError):
    """Raised when a source references a JSON extraction profile that is unknown."""


#: Registry of JSON extraction profiles keyed by name. This stands in for the
#: provider-config-supplied profiles a later slice will load from YAML; the point
#: is that provider-specific keys live here as data, not in adapter code.
JSON_EXTRACTION_PROFILES: dict[str, JsonExtractionProfile] = {
    "offer_api": JsonExtractionProfile(
        name="offer_api",
        records_path=("offers",),
        provider_key="provider",
        fields=(
            JsonField("service", "service", "text"),
            JsonField("offer_type", "billing", "text"),
            JsonField("requires_card", "card_required", "bool"),
            JsonField("has_paid_dependencies", "paid_addons", "bool"),
            JsonField("quotas", "quotas", "list"),
        ),
        required_fields=("service", "offer_type"),
    ),
    "pricing_api": JsonExtractionProfile(
        name="pricing_api",
        records_path=("data", "plans"),
        provider_key="vendor",
        fields=(
            JsonField("service", "plan", "text"),
            JsonField("offer_type", "tier", "text"),
            JsonField("requires_card", "credit_card_required", "bool"),
            JsonField("has_paid_dependencies", "requires_paid_addon", "bool"),
            JsonField("quotas", "limits", "list"),
        ),
        required_fields=("service", "offer_type"),
    ),
}


def resolve_json_profile(name: str | None) -> JsonExtractionProfile:
    """Return the named profile or raise :class:`UnknownJsonProfileError`."""

    try:
        return JSON_EXTRACTION_PROFILES[name]  # type: ignore[index]
    except KeyError as exc:
        raise UnknownJsonProfileError(
            f"No JSON extraction profile named '{name}'; known: {sorted(JSON_EXTRACTION_PROFILES)}."
        ) from exc


class StructuredApiAdapter(SourceAdapter):
    """Adapter that extracts candidate facts from a structured JSON endpoint."""

    name = "structured-api"

    def __init__(
        self,
        fetcher: Fetcher,
        source_urls: Sequence[str],
        profile: JsonExtractionProfile,
        *,
        provider: str | None = None,
    ) -> None:
        super().__init__(fetcher)
        self._source_urls = tuple(source_urls)
        self._profile = profile
        self._provider = provider

    # -- contract methods --------------------------------------------------

    def discover(self) -> Sequence[str]:
        return self._source_urls

    def fetch(self, url: str) -> FetchResult:
        # The sole network path -- delegated to the injected safe fetcher.
        return self.fetcher.fetch(url)

    def canonicalize(self, result: FetchResult) -> SourceDocument:
        # Best-effort decode; never raises. Parsing (and its error handling) is
        # deferred to extract() so canonicalize can never crash the scan.
        canonical = result.content.decode("utf-8", errors="replace")
        return SourceDocument(
            url=result.final_url,
            mime=result.mime,
            content_hash=result.content_hash,
            fetched_at=result.fetched_at,
            raw=result.content,
            canonical=canonical,
        )

    def extract(self, document: SourceDocument) -> Sequence[CandidateFacts]:
        default_provider = self._provider or host(document.url) or "unknown"

        extraction = extract_records(
            document.canonical, self._profile, excerpt_limit=_EXCERPT_LIMIT
        )
        if extraction.error is not None:
            return [
                self._rejected(
                    document,
                    default_provider,
                    extraction.error.error,
                    extraction.error.detail,
                )
            ]

        provider = self._provider or extraction.provider or host(document.url) or "unknown"
        path_label = ".".join(self._profile.records_path) or "$"
        candidates: list[CandidateFacts] = []
        for index, record in enumerate(extraction.records):
            location = EvidenceLocation(
                url=document.url,
                selector=f"$.{path_label}[{index}]",
                excerpt=record.excerpt or None,
                content_hash=document.content_hash,
            )
            candidates.append(
                CandidateFacts(
                    provider=provider,
                    source_url=document.url,
                    facts=dict(record.facts),
                    evidence=(location,),
                    verification_state="candidate",
                )
            )
        return candidates

    def validate(self, candidate: CandidateFacts) -> Sequence[str]:
        facts = candidate.facts
        if "error" in facts:
            detail = facts.get("detail")
            suffix = f" ({detail})" if detail else ""
            return [f"Document rejected: {facts['error']}{suffix}"]

        problems: list[str] = []
        for field_name in self._profile.required_fields:
            if facts.get(field_name) in (None, ""):
                problems.append(f"Missing required field '{field_name}'.")
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
            detail=f"Endpoint reachable (profile={self._profile.name}).",
            source_url=probe,
        )

    # -- internals ---------------------------------------------------------

    def _rejected(
        self,
        document: SourceDocument,
        provider: str,
        error: str,
        detail: str = "",
    ) -> CandidateFacts:
        location = EvidenceLocation(url=document.url, content_hash=document.content_hash)
        return CandidateFacts(
            provider=provider,
            source_url=document.url,
            facts={"error": error, "detail": detail},
            evidence=(location,),
            verification_state="rejected",
        )


__all__ = (
    "StructuredApiAdapter",
    "JSON_EXTRACTION_PROFILES",
    "resolve_json_profile",
    "UnknownJsonProfileError",
)
