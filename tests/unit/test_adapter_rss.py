"""Contract tests for the RSS/Atom source adapter (offline, fixture-driven).

Each fixture state under ``tests/fixtures/ingest/example/rss/<state>/`` pairs a
``source.xml`` with an ``expected.json`` describing the candidate facts and
evidence the adapter must produce. The tests drive all seven
:class:`~app.ingest.base.SourceAdapter` contract methods and prove the hard
rules: malformed/partial input yields a *handled* outcome (rejected candidate or
UNKNOWN facts) that :meth:`validate` flags -- never a crash and never a guessed
value -- and a non-allowlisted URL is refused by the shared safe fetcher.
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
    RssFeedAdapter,
    SourceDocument,
)
from app.ingest.fetch import DisallowedHostError
from app.ingest.scan import build_adapter
from app.models.domain import Source

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingest" / "example" / "rss"
_STATES = ("unchanged", "changed", "malformed", "partial", "contradictory")
_MATERIAL_KEYS = ("service", "offer_type", "requires_card", "has_paid_dependencies", "quotas")


def _norm(value: object) -> object:
    """Normalise tuples to lists so fixture JSON compares equal to adapter output."""

    if isinstance(value, tuple):
        return [_norm(v) for v in value]
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


def _load(state: str) -> tuple[bytes, dict]:
    source = (_FIXTURES / state / "source.xml").read_bytes()
    expected = json.loads((_FIXTURES / state / "expected.json").read_text(encoding="utf-8"))
    return source, expected


def _adapter(state: str) -> tuple[RssFeedAdapter, dict]:
    source_bytes, expected = _load(state)
    url = expected["source_url"]
    policy = FetchPolicy(official_domains=("example.com",))
    fetcher = FixtureFetcher({url: (source_bytes, expected["mime"])}, policy)
    return RssFeedAdapter(fetcher, source_urls=(url,)), expected


@pytest.mark.parametrize("state", _STATES)
def test_rss_contract_matches_fixture(state: str) -> None:
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


def test_rss_health_ok_offline() -> None:
    adapter, expected = _adapter("unchanged")
    health = adapter.health()  # the seventh contract method
    assert isinstance(health, AdapterHealth)
    assert health.healthy is True
    assert health.source_url == expected["source_url"]


def test_rss_health_unhealthy_when_source_unreachable() -> None:
    policy = FetchPolicy(official_domains=("example.com",))
    adapter = RssFeedAdapter(
        FixtureFetcher({}, policy), source_urls=("https://example.com/feed.xml",)
    )
    health = adapter.health()
    assert health.healthy is False
    assert "not_found" in health.detail


def test_rss_partial_never_guesses_missing_material_facts() -> None:
    adapter, _ = _adapter("partial")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    (candidate,) = adapter.extract(document)
    # Missing offer_type/booleans/link are UNKNOWN (None), never fabricated.
    assert candidate.facts["offer_type"] is None
    assert candidate.facts["requires_card"] is None
    assert candidate.facts["has_paid_dependencies"] is None
    assert candidate.facts["link"] is None
    problems = adapter.validate(candidate)
    assert any("offer_type" in p for p in problems)
    assert any("link" in p for p in problems)


@pytest.mark.parametrize(
    "payload",
    [
        b"not xml at all",
        b"<rss><channel><item><title>x</title></item>",  # truncated
        (
            b'<?xml version="1.0"?>'
            b"<!DOCTYPE lolz [<!ENTITY lol 'lol'>]>"
            b'<rss version="2.0"><channel></channel></rss>'
        ),  # DTD / XXE attempt
        b'<?xml version="1.0"?><html><body>surprise</body></html>',  # wrong root
    ],
)
def test_rss_malformed_input_never_crashes_or_guesses(payload: bytes) -> None:
    url = "https://example.com/feed.xml"
    policy = FetchPolicy(official_domains=("example.com",))
    fetcher = FixtureFetcher({url: (payload, "application/rss+xml")}, policy)
    adapter = RssFeedAdapter(fetcher, source_urls=(url,))

    # Must not raise -- a handled rejected candidate is returned instead.
    document = adapter.canonicalize(adapter.fetch(url))
    candidates = adapter.extract(document)
    assert len(candidates) == 1
    (candidate,) = candidates
    assert candidate.verification_state == "rejected"
    assert "error" in candidate.facts
    assert not any(key in candidate.facts for key in _MATERIAL_KEYS)
    assert adapter.validate(candidate)  # captured validation failure


def test_rss_contradictory_extracts_both_unresolved() -> None:
    adapter, expected = _adapter("contradictory")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    candidates = adapter.extract(document)
    assert len(candidates) == 2
    # Same service, conflicting offer_type; the adapter reports both verbatim and
    # does NOT merge or pick a winner (that is reconciliation's job, not the
    # adapter's -- "unknown is better than guessed").
    assert {c.facts["service"] for c in candidates} == {"Workers"}
    assert {c.facts["offer_type"] for c in candidates} == set(expected["contradiction"]["values"])


def test_rss_non_allowlisted_url_is_refused() -> None:
    policy = FetchPolicy(official_domains=("example.com",))
    fetcher = FixtureFetcher(
        {"https://evil.test/feed.xml": (b"<rss/>", "application/rss+xml")}, policy
    )
    adapter = RssFeedAdapter(fetcher, source_urls=("https://evil.test/feed.xml",))
    with pytest.raises(DisallowedHostError):
        adapter.fetch("https://evil.test/feed.xml")


def test_rss_uses_only_the_fetcher_seam(monkeypatch) -> None:
    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("adapter opened a socket directly")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    adapter, _ = _adapter("unchanged")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    assert adapter.extract(document)


def test_build_adapter_resolves_rss_type() -> None:
    source = Source(
        adapter_type="rss",
        trust_level="official",
        endpoint="https://example.com/feed.xml",
    )
    policy = FetchPolicy(official_domains=("example.com",))
    adapter = build_adapter(source, FixtureFetcher({}, policy))
    assert isinstance(adapter, RssFeedAdapter)
    assert adapter.discover() == ("https://example.com/feed.xml",)
