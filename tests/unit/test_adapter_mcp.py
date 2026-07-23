"""Contract tests for the MCP (Model Context Protocol) tool source adapter.

Offline, fixture-driven, with **zero** real network or process I/O: the MCP
transport is an injected fake (:class:`FixtureMcpClient`) exactly as the HTTP
adapters inject a :class:`FixtureFetcher`. Beyond the seven-method contract
matrix (mirroring the HTTP adapters) these tests prove the two mandatory MCP
safety seams in BOTH directions:

* the strict **capability allowlist** -- an allowed tool is invoked, a tool
  outside the allowlist is refused with :class:`DisallowedCapabilityError`
  *before the client is ever touched*;
* the shared safe-fetch **host policy** -- a non-allowlisted MCP host is refused
  with :class:`DisallowedHostError` before any invocation;

and that the injected offline client performs no socket I/O (a socket-forbidden
monkeypatch seam test), while the default :class:`OfflineMcpClient` refuses live
invocation outright.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from app.ingest import (
    AdapterHealth,
    CandidateFacts,
    EvidenceLocation,
    FetchPolicy,
    OfflineFetcher,
    SourceDocument,
)
from app.ingest.adapters.mcp import (
    MCP_PROFILES,
    DisallowedCapabilityError,
    McpDisabledError,
    McpSourceProfile,
    McpToolAdapter,
    McpToolResult,
    OfflineMcpClient,
    UnknownMcpProfileError,
    resolve_mcp_profile,
)
from app.ingest.fetch import DisallowedHostError, FetchError
from app.ingest.scan import build_adapter
from app.models.domain import Source

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingest" / "example" / "mcp"
_STATES = ("unchanged", "changed", "malformed", "partial", "contradictory")
_MATERIAL_KEYS = ("service", "offer_type", "requires_card", "has_paid_dependencies", "quotas")
_PROFILE = MCP_PROFILES["mcp_offer_catalogue"]


class FixtureMcpClient:
    """An offline fake MCP client. Records every call; opens no socket, spawns
    no process. It is the MCP analogue of :class:`FixtureFetcher`."""

    def __init__(self, content: bytes, mime: str = "application/json") -> None:
        self._content = content
        self._mime = mime
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def call_tool(self, url: str, tool: str, arguments: Mapping[str, Any]) -> McpToolResult:
        self.calls.append((url, tool, dict(arguments)))
        return McpToolResult(tool=tool, content=self._content, mime=self._mime)


def _norm(value: object) -> object:
    if isinstance(value, (tuple, list)):
        return [_norm(v) for v in value]
    return value


def _load(state: str) -> tuple[bytes, dict]:
    source = (_FIXTURES / state / "source.json").read_bytes()
    expected = json.loads((_FIXTURES / state / "expected.json").read_text(encoding="utf-8"))
    return source, expected


def _adapter(state: str) -> tuple[McpToolAdapter, FixtureMcpClient, dict]:
    source_bytes, expected = _load(state)
    url = expected["source_url"]
    policy = FetchPolicy(official_domains=("example.com",))
    client = FixtureMcpClient(source_bytes, expected["mime"])
    adapter = McpToolAdapter(
        OfflineFetcher(policy), client=client, source_urls=(url,), profile=_PROFILE
    )
    return adapter, client, expected


@pytest.mark.parametrize("state", _STATES)
def test_mcp_contract_matches_fixture(state: str) -> None:
    adapter, _client, expected = _adapter(state)

    # discover -> fetch -> canonicalize -> extract (four of the seven methods).
    urls = adapter.discover()
    assert urls == (expected["source_url"],)
    document = adapter.canonicalize(adapter.fetch(urls[0]))
    assert isinstance(document, SourceDocument)

    candidates = adapter.extract(document)
    assert len(candidates) == expected["candidate_count"]

    for candidate, want in zip(candidates, expected["candidates"], strict=True):
        assert isinstance(candidate, CandidateFacts)
        assert candidate.verification_state == want["verification_state"]
        assert candidate.verification_state != "verified"

        problems = adapter.validate(candidate)
        evidence = adapter.evidence(candidate)
        assert evidence and isinstance(evidence[0], EvidenceLocation)
        assert evidence[0].url == want["evidence_url"]
        assert evidence[0].selector == want["evidence_selector"]
        assert evidence[0].content_hash == document.content_hash

        if candidate.verification_state == "rejected":
            assert candidate.facts.get("error") == want["facts"]["error"]
            assert problems, "a rejected candidate must be flagged by validate()"
            assert not any(key in candidate.facts for key in _MATERIAL_KEYS)
            continue

        for key, value in want["facts"].items():
            assert _norm(candidate.facts.get(key)) == _norm(value), key

        if want["expect_valid"]:
            assert problems == []
        else:
            assert problems, "a partial candidate must be flagged by validate()"
            for token in want.get("validate_contains", []):
                assert any(token in p for p in problems), token


def test_mcp_health_ok_offline() -> None:
    adapter, _client, expected = _adapter("unchanged")
    health = adapter.health()  # the seventh contract method
    assert isinstance(health, AdapterHealth)
    assert health.healthy is True
    assert health.source_url == expected["source_url"]


def test_mcp_health_unhealthy_with_default_offline_client() -> None:
    policy = FetchPolicy(official_domains=("example.com",))
    adapter = McpToolAdapter(
        OfflineFetcher(policy),
        client=OfflineMcpClient(),
        source_urls=("https://mcp.example.com/servers/offers",),
        profile=_PROFILE,
    )
    health = adapter.health()
    assert health.healthy is False
    assert "mcp_disabled" in health.detail


def test_mcp_partial_never_guesses_missing_material_facts() -> None:
    adapter, _client, _ = _adapter("partial")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    (candidate,) = adapter.extract(document)
    assert candidate.facts["offer_type"] is None
    assert candidate.facts["requires_card"] is None
    assert candidate.facts["has_paid_dependencies"] is None
    assert candidate.facts["quotas"] == ()
    problems = adapter.validate(candidate)
    assert any("offer_type" in p for p in problems)


@pytest.mark.parametrize(
    "payload",
    [
        b"not json at all",
        b'{"provider": "example", "results": [',  # truncated
        b'{"provider": "example", "results": {"not": "a list"}}',  # not a list
        b'{"provider": "example"}',  # records path absent
        b'"just a string"',  # non-mapping root
    ],
)
def test_mcp_malformed_result_never_crashes_or_guesses(payload: bytes) -> None:
    url = "https://mcp.example.com/servers/offers"
    policy = FetchPolicy(official_domains=("example.com",))
    client = FixtureMcpClient(payload)
    adapter = McpToolAdapter(
        OfflineFetcher(policy), client=client, source_urls=(url,), profile=_PROFILE
    )

    document = adapter.canonicalize(adapter.fetch(url))
    candidates = adapter.extract(document)
    assert len(candidates) == 1
    (candidate,) = candidates
    assert candidate.verification_state == "rejected"
    assert "error" in candidate.facts
    assert not any(key in candidate.facts for key in _MATERIAL_KEYS)
    assert adapter.validate(candidate)


def test_mcp_contradictory_extracts_both_unresolved() -> None:
    adapter, _client, expected = _adapter("contradictory")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    candidates = adapter.extract(document)
    assert len(candidates) == 2
    assert {c.facts["service"] for c in candidates} == {expected["contradiction"]["shared"]}
    assert {c.facts["offer_type"] for c in candidates} == set(expected["contradiction"]["values"])


# --- capability allowlist: BOTH directions ---------------------------------


def test_mcp_allowlist_allows_sanctioned_tool() -> None:
    adapter, client, expected = _adapter("unchanged")
    # Direction 1: the profile's tool IS in the allowlist -> invoked normally.
    assert adapter.allows("list_free_offers") is True
    result = adapter.fetch(expected["source_url"])
    assert result.status == 200
    assert len(client.calls) == 1
    assert client.calls[0][1] == "list_free_offers"


def test_mcp_allowlist_refuses_unsanctioned_tool_before_any_invocation() -> None:
    # Direction 2: a profile whose tool is OUTSIDE its own allowlist is refused
    # BEFORE the client is ever touched.
    policy = FetchPolicy(official_domains=("example.com",))
    client = FixtureMcpClient(b'{"provider": "example", "results": []}')
    disallowed = McpSourceProfile(
        name="disallowed",
        tool="drop_tables",
        allowed_capabilities=frozenset({"list_free_offers"}),
        extraction=_PROFILE.extraction,
    )
    adapter = McpToolAdapter(
        OfflineFetcher(policy),
        client=client,
        source_urls=("https://mcp.example.com/servers/offers",),
        profile=disallowed,
    )
    assert adapter.allows("drop_tables") is False
    with pytest.raises(DisallowedCapabilityError):
        adapter.fetch("https://mcp.example.com/servers/offers")
    # Proof the refusal happened pre-invocation: the client was never called.
    assert client.calls == []


def test_mcp_disallowed_capability_is_a_fetch_error() -> None:
    # run_scan handles it as an ordinary per-URL error (additive-only).
    assert issubclass(DisallowedCapabilityError, FetchError)
    assert issubclass(McpDisabledError, FetchError)


# --- injectable offline client: no real I/O --------------------------------


def test_mcp_default_client_refuses_live_invocation() -> None:
    with pytest.raises(McpDisabledError):
        OfflineMcpClient().call_tool("https://mcp.example.com/x", "list_free_offers", {})


def test_mcp_injected_client_performs_no_real_io(monkeypatch) -> None:
    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("MCP adapter opened a socket / resolved DNS directly")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)

    adapter, client, expected = _adapter("unchanged")
    document = adapter.canonicalize(adapter.fetch(expected["source_url"]))
    candidates = adapter.extract(document)
    assert candidates
    # The injected seam -- not a socket -- provided the data.
    assert len(client.calls) == 1


# --- shared safe-fetch host policy -----------------------------------------


def test_mcp_non_allowlisted_url_is_refused_before_invocation() -> None:
    policy = FetchPolicy(official_domains=("example.com",))
    client = FixtureMcpClient(b'{"provider": "x", "results": []}')
    adapter = McpToolAdapter(
        OfflineFetcher(policy),
        client=client,
        source_urls=("https://evil.test/servers/offers",),
        profile=_PROFILE,
    )
    with pytest.raises(DisallowedHostError):
        adapter.fetch("https://evil.test/servers/offers")
    # Host gate runs before the client -- so no invocation happened.
    assert client.calls == []


# --- registry wiring -------------------------------------------------------


def test_resolve_mcp_profile_unknown_raises() -> None:
    with pytest.raises(UnknownMcpProfileError):
        resolve_mcp_profile("no_such_profile")


def test_build_adapter_resolves_mcp_type_with_offline_default_client() -> None:
    source = Source(
        adapter_type="mcp",
        trust_level="official",
        endpoint="https://mcp.example.com/servers/offers",
        parser_profile="mcp_offer_catalogue",
    )
    policy = FetchPolicy(official_domains=("example.com",))
    adapter = build_adapter(source, OfflineFetcher(policy))
    assert isinstance(adapter, McpToolAdapter)
    assert adapter.discover() == ("https://mcp.example.com/servers/offers",)
    # The registry default is the safe offline client: live invocation is refused.
    with pytest.raises(McpDisabledError):
        adapter.fetch("https://mcp.example.com/servers/offers")
