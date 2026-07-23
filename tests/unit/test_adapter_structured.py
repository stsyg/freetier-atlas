"""Contract tests for the structured-API (JSON/REST) source adapter.

Offline, fixture-driven. Each state under
``tests/fixtures/ingest/example/structured/<state>/`` pairs a ``source.json``
with an ``expected.json`` describing the candidate facts and evidence the adapter
must produce. The tests drive all seven
:class:`~app.ingest.base.SourceAdapter` contract methods and prove the hard
rules: malformed/partial input yields a *handled* outcome (rejected candidate or
UNKNOWN facts) that :meth:`validate` flags -- never a crash, never a guessed
value -- and a non-allowlisted URL is refused by the shared safe fetcher. The
network is reached only through the injected :class:`FixtureFetcher` seam.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from app.ingest import (
    AdapterHealth,
    CandidateFacts,
    EvidenceLocation,
    FetchPolicy,
    FixtureFetcher,
    SourceDocument,
    StructuredApiAdapter,
)
from app.ingest.adapters._json import MAX_JSON_NESTING_DEPTH
from app.ingest.adapters.structured import (
    JSON_EXTRACTION_PROFILES,
    UnknownJsonProfileError,
    resolve_json_profile,
)
from app.ingest.fetch import DisallowedHostError
from app.ingest.scan import build_adapter
from app.models.domain import Source

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingest" / "example" / "structured"
_STATES = ("unchanged", "changed", "malformed", "partial", "contradictory")
_MATERIAL_KEYS = ("service", "offer_type", "requires_card", "has_paid_dependencies", "quotas")
_PROFILE = JSON_EXTRACTION_PROFILES["offer_api"]


def _norm(value: object) -> object:
    if isinstance(value, (tuple, list)):
        return [_norm(v) for v in value]
    return value


def _load(state: str) -> tuple[bytes, dict]:
    source = (_FIXTURES / state / "source.json").read_bytes()
    expected = json.loads((_FIXTURES / state / "expected.json").read_text(encoding="utf-8"))
    return source, expected


def _adapter(state: str) -> tuple[StructuredApiAdapter, dict]:
    source_bytes, expected = _load(state)
    url = expected["source_url"]
    policy = FetchPolicy(official_domains=("example.com",))
    fetcher = FixtureFetcher({url: (source_bytes, expected["mime"])}, policy)
    return StructuredApiAdapter(fetcher, source_urls=(url,), profile=_PROFILE), expected


@pytest.mark.parametrize("state", _STATES)
def test_structured_contract_matches_fixture(state: str) -> None:
    adapter, expected = _adapter(state)

    # discover -> fetch -> canonicalize -> extract (four of the seven methods).
    urls = adapter.discover()
    assert urls == (expected["source_url"],)
    document = adapter.canonicalize(adapter.fetch(urls[0]))
    assert isinstance(document, SourceDocument)

    candidates = adapter.extract(document)
    assert len(candidates) == expected["candidate_count"]

    for candidate, want in zip(candidates, expected["candidates"], strict=True):
        assert isinstance(candidate, CandidateFacts)
        # Adapters only ever emit pre-publication states -- never "verified".
        assert candidate.verification_state == want["verification_state"]
        assert candidate.verification_state != "verified"

        # validate + evidence (two more methods).
        problems = adapter.validate(candidate)
        evidence = adapter.evidence(candidate)
        assert evidence and isinstance(evidence[0], EvidenceLocation)
        assert evidence[0].url == want["evidence_url"]
        assert evidence[0].selector == want["evidence_selector"]
        assert evidence[0].content_hash == document.content_hash

        if candidate.verification_state == "rejected":
            # Handled malformed outcome: a captured validation failure, and the
            # facts describe ONLY the error -- no material value is guessed.
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


def test_structured_health_ok_offline() -> None:
    adapter, expected = _adapter("unchanged")
    health = adapter.health()  # the seventh contract method
    assert isinstance(health, AdapterHealth)
    assert health.healthy is True
    assert health.source_url == expected["source_url"]


def test_structured_health_unhealthy_when_source_unreachable() -> None:
    policy = FetchPolicy(official_domains=("example.com",))
    adapter = StructuredApiAdapter(
        FixtureFetcher({}, policy),
        source_urls=("https://example.com/api/offers.json",),
        profile=_PROFILE,
    )
    health = adapter.health()
    assert health.healthy is False
    assert "not_found" in health.detail


def test_structured_partial_never_guesses_missing_material_facts() -> None:
    adapter, _ = _adapter("partial")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    (candidate,) = adapter.extract(document)
    # Missing offer_type / booleans are UNKNOWN (None); absent list is empty.
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
        b'{"provider": "example", "offers": [',  # truncated
        b'{"provider": "example", "offers": {"not": "a list"}}',  # records not a list
        b'{"provider": "example"}',  # records path absent
        b"[1, 2, 3]",  # non-mapping root
        # Deterministic recursion-bomb guard: nesting deeper than the portable
        # depth cap is rejected BEFORE json.loads on every platform (not reliant
        # on RecursionError, whose threshold varies by interpreter/OS).
        b'{"provider": "example", "offers": '
        + b"[" * (MAX_JSON_NESTING_DEPTH + 50)
        + b"]" * (MAX_JSON_NESTING_DEPTH + 50)
        + b"}",
    ],
)
def test_structured_malformed_input_never_crashes_or_guesses(payload: bytes) -> None:
    url = "https://example.com/api/offers.json"
    policy = FetchPolicy(official_domains=("example.com",))
    fetcher = FixtureFetcher({url: (payload, "application/json")}, policy)
    adapter = StructuredApiAdapter(fetcher, source_urls=(url,), profile=_PROFILE)

    # Must not raise -- a handled rejected candidate is returned instead.
    document = adapter.canonicalize(adapter.fetch(url))
    candidates = adapter.extract(document)
    assert len(candidates) == 1
    (candidate,) = candidates
    assert candidate.verification_state == "rejected"
    assert "error" in candidate.facts
    assert not any(key in candidate.facts for key in _MATERIAL_KEYS)
    assert adapter.validate(candidate)  # captured validation failure


def test_structured_contradictory_extracts_both_unresolved() -> None:
    adapter, expected = _adapter("contradictory")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    candidates = adapter.extract(document)
    assert len(candidates) == 2
    # Same service, conflicting offer_type; the adapter reports both verbatim and
    # does NOT merge or pick a winner ("unknown is better than guessed").
    assert {c.facts["service"] for c in candidates} == {expected["contradiction"]["shared"]}
    assert {c.facts["offer_type"] for c in candidates} == set(expected["contradiction"]["values"])


def test_structured_non_allowlisted_url_is_refused() -> None:
    policy = FetchPolicy(official_domains=("example.com",))
    fetcher = FixtureFetcher(
        {"https://evil.test/api/offers.json": (b"{}", "application/json")}, policy
    )
    adapter = StructuredApiAdapter(
        fetcher, source_urls=("https://evil.test/api/offers.json",), profile=_PROFILE
    )
    with pytest.raises(DisallowedHostError):
        adapter.fetch("https://evil.test/api/offers.json")


def test_structured_uses_only_the_fetcher_seam(monkeypatch) -> None:
    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("adapter opened a socket directly")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    adapter, _ = _adapter("unchanged")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    assert adapter.extract(document)


def test_resolve_json_profile_unknown_raises() -> None:
    with pytest.raises(UnknownJsonProfileError):
        resolve_json_profile("no_such_profile")


def test_build_adapter_resolves_structured_type() -> None:
    source = Source(
        adapter_type="structured-api",
        trust_level="official",
        endpoint="https://example.com/api/offers.json",
        parser_profile="offer_api",
    )
    policy = FetchPolicy(official_domains=("example.com",))
    adapter = build_adapter(source, FixtureFetcher({}, policy))
    assert isinstance(adapter, StructuredApiAdapter)
    assert adapter.discover() == ("https://example.com/api/offers.json",)
