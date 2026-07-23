"""Model Context Protocol (MCP) tool source adapter (standard library only).

Consumes an official MCP tool source: it invokes a named MCP *tool* and parses
its JSON result into *candidate* facts. There is **no** MCP SDK dependency -- the
MCP transport is modelled as an injectable :class:`McpClient` Protocol so the
adapter is exercised in tests with an offline fake that performs zero real
network or process I/O, exactly as the HTTP adapters inject a
:class:`~app.ingest.fetch.FixtureFetcher`.

Two safety seams, both mandatory (docs/SECURITY_PRIVACY_ABUSE.md):

* **Capability allowlist.** Only tools explicitly named in the source profile's
  ``allowed_capabilities`` may be invoked. A tool outside the allowlist is
  refused with :class:`DisallowedCapabilityError` **before the MCP client is ever
  called** -- an untrusted server can never coax the adapter into invoking a tool
  the operator did not sanction.
* **Shared safe-fetch host policy.** The MCP server URL is gated through the same
  scheme + official-domain allowlist the HTTP adapters use, so a non-allowlisted
  host is refused (:class:`~app.ingest.fetch.DisallowedHostError`) before any
  invocation.

Both refusals -- and the offline default client -- subclass
:class:`~app.ingest.fetch.FetchError`, so :func:`app.ingest.scan.run_scan`
handles them as ordinary per-URL errors without any orchestration change.

Design contract (docs/AGENT_HARNESS.md "unknown is better than guessed"): a
malformed tool result yields a single ``rejected`` candidate carrying only
``{error, detail}`` (no material key); a partial record carries ``None`` for its
missing fields; contradictory records are both emitted, unresolved. The adapter
never guesses a value and never raises an uncaught exception during extraction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

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
from app.ingest.fetch import (
    Fetcher,
    FetchError,
    FetchResult,
    check_host,
    check_scheme,
    content_hash,
)

_EXCERPT_LIMIT = 280


# --- MCP-specific typed errors (both FetchError so run_scan handles them) ---


class McpError(FetchError):
    """Base class for MCP-adapter fetch-time rejections."""

    reason = "mcp_error"


class DisallowedCapabilityError(McpError):
    """A tool outside the capability allowlist was requested (refused pre-call)."""

    reason = "disallowed_capability"


class McpDisabledError(McpError):
    """The default offline MCP client refused a live tool invocation."""

    reason = "mcp_disabled"


# --- MCP client seam (injectable; no SDK, no real I/O in the default) -------


@dataclass(frozen=True)
class McpToolResult:
    """The result of one MCP tool invocation.

    ``content`` is the tool's JSON payload as bytes (hashed for provenance);
    ``mime`` defaults to ``application/json``.
    """

    tool: str
    content: bytes
    mime: str = "application/json"


@runtime_checkable
class McpClient(Protocol):
    """The MCP transport seam. The adapter depends on this, never on an SDK."""

    def call_tool(
        self, url: str, tool: str, arguments: Mapping[str, Any]
    ) -> McpToolResult:  # pragma: no cover - protocol
        ...


class OfflineMcpClient:
    """The safe default MCP client: it never opens a socket or spawns a process.

    Every :meth:`call_tool` raises :class:`McpDisabledError`, so the default
    posture is "no MCP egress". Tests inject an offline fake client instead.
    """

    def call_tool(self, url: str, tool: str, arguments: Mapping[str, Any]) -> McpToolResult:
        raise McpDisabledError(
            "Live MCP tool invocation is disabled; inject an MCP client to enable it."
        )


# --- Declarative source profile (tool + allowlist + JSON extraction) --------


@dataclass(frozen=True)
class McpSourceProfile:
    """A declarative recipe for one MCP source.

    ``tool`` is the single tool this source invokes; ``allowed_capabilities`` is
    the strict allowlist it must belong to; ``extraction`` maps the tool result
    JSON to candidate facts; ``arguments`` are passed verbatim to the tool. All
    provider-specific knowledge is data here, not code.
    """

    name: str
    tool: str
    allowed_capabilities: frozenset[str]
    extraction: JsonExtractionProfile
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_capabilities", frozenset(self.allowed_capabilities))
        object.__setattr__(self, "arguments", dict(self.arguments))


class UnknownMcpProfileError(ValueError):
    """Raised when a source references an MCP profile that is unknown."""


#: Registry of MCP source profiles keyed by name. Stands in for the
#: provider-config-supplied profiles a later slice loads from YAML.
MCP_PROFILES: dict[str, McpSourceProfile] = {
    "mcp_offer_catalogue": McpSourceProfile(
        name="mcp_offer_catalogue",
        tool="list_free_offers",
        allowed_capabilities=frozenset({"list_free_offers"}),
        extraction=JsonExtractionProfile(
            name="mcp_offer_catalogue",
            records_path=("results",),
            provider_key="provider",
            fields=(
                JsonField("service", "name", "text"),
                JsonField("offer_type", "plan", "text"),
                JsonField("requires_card", "needs_card", "bool"),
                JsonField("has_paid_dependencies", "extra_paid", "bool"),
                JsonField("quotas", "limits", "list"),
            ),
            required_fields=("service", "offer_type"),
        ),
    ),
}


def resolve_mcp_profile(name: str | None) -> McpSourceProfile:
    """Return the named MCP profile or raise :class:`UnknownMcpProfileError`."""

    try:
        return MCP_PROFILES[name]  # type: ignore[index]
    except KeyError as exc:
        raise UnknownMcpProfileError(
            f"No MCP profile named '{name}'; known: {sorted(MCP_PROFILES)}."
        ) from exc


class McpToolAdapter(SourceAdapter):
    """Adapter that extracts candidate facts from an allowlisted MCP tool result."""

    name = "mcp"

    def __init__(
        self,
        fetcher: Fetcher,
        client: McpClient,
        source_urls: Sequence[str],
        profile: McpSourceProfile,
        *,
        provider: str | None = None,
    ) -> None:
        super().__init__(fetcher)
        self._client = client
        self._source_urls = tuple(source_urls)
        self._profile = profile
        self._provider = provider

    # -- capability allowlist ----------------------------------------------

    def allows(self, tool: str) -> bool:
        """True if ``tool`` is in this source's strict capability allowlist."""

        return tool in self._profile.allowed_capabilities

    # -- contract methods --------------------------------------------------

    def discover(self) -> Sequence[str]:
        return self._source_urls

    def fetch(self, url: str) -> FetchResult:
        # 1. Shared safe-fetch host policy gate (scheme + official-domain
        #    allowlist), run BEFORE any invocation so a non-allowlisted host is
        #    refused pre-connection -- identical policy to the HTTP adapters.
        self._gate_url(url)

        # 2. Strict capability allowlist, enforced BEFORE the client is touched.
        tool = self._profile.tool
        if not self.allows(tool):
            raise DisallowedCapabilityError(
                f"MCP tool '{tool}' is not in the capability allowlist "
                f"{sorted(self._profile.allowed_capabilities)}; refusing to invoke."
            )

        # 3. Only now is the injected client seam invoked (no SDK, no direct I/O).
        result = self._client.call_tool(url, tool, self._profile.arguments)
        return FetchResult(
            content=result.content,
            mime=result.mime,
            final_url=url,
            content_hash=content_hash(result.content),
            fetched_at=datetime.now(UTC),
            status=200,
        )

    def canonicalize(self, result: FetchResult) -> SourceDocument:
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
        profile = self._profile.extraction

        extraction = extract_records(document.canonical, profile, excerpt_limit=_EXCERPT_LIMIT)
        if extraction.error is not None:
            return [
                self._rejected(
                    document, default_provider, extraction.error.error, extraction.error.detail
                )
            ]

        provider = self._provider or extraction.provider or host(document.url) or "unknown"
        path_label = ".".join(profile.records_path) or "$"
        candidates: list[CandidateFacts] = []
        for index, record in enumerate(extraction.records):
            location = EvidenceLocation(
                url=document.url,
                selector=f"{self._profile.tool}:{path_label}[{index}]",
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
            return [f"Tool result rejected: {facts['error']}{suffix}"]

        problems: list[str] = []
        for field_name in self._profile.extraction.required_fields:
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
            detail=f"MCP tool reachable (profile={self._profile.name}, tool={self._profile.tool}).",
            source_url=probe,
        )

    # -- internals ---------------------------------------------------------

    def _gate_url(self, url: str) -> None:
        """Apply the shared safe-fetch scheme + host policy to the MCP URL.

        Reuses the fetcher's :class:`~app.ingest.fetch.FetchPolicy` so the MCP
        adapter is bound by exactly the same allowlist as the HTTP adapters.
        """

        policy = getattr(self._fetcher, "policy", None)
        if policy is None:
            return
        check_scheme(url, policy.allowed_schemes)
        check_host(url, policy.official_domains)

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
    "McpToolAdapter",
    "McpClient",
    "McpToolResult",
    "OfflineMcpClient",
    "McpSourceProfile",
    "MCP_PROFILES",
    "resolve_mcp_profile",
    "UnknownMcpProfileError",
    "McpError",
    "DisallowedCapabilityError",
    "McpDisabledError",
)
