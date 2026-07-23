"""Contract tests for the static-doc / HTML source adapter (offline, fixtures).

Each fixture state under ``tests/fixtures/ingest/example/html/<state>/`` pairs a
``source.html`` with an ``expected.json`` (including the extraction ``profile``)
describing the candidate facts and evidence the adapter must produce. The tests
drive all seven :class:`~app.ingest.base.SourceAdapter` contract methods and
prove the hard rules: a missing table yields a handled *rejected* candidate, a
missing column yields UNKNOWN (``None``) facts -- never a crash and never a
guessed value -- and a non-allowlisted URL is refused by the shared fetcher. All
provider-specific selectors live in the extraction profile (data), not in code.
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
    HtmlDocAdapter,
    SourceDocument,
    UnknownProfileError,
    resolve_profile,
)
from app.ingest.fetch import DisallowedHostError
from app.ingest.scan import build_adapter
from app.models.domain import Source

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingest" / "example" / "html"
_STATES = ("unchanged", "changed", "malformed", "partial", "contradictory")
_MATERIAL_KEYS = ("service", "offer_type", "requires_card", "has_paid_dependencies", "quotas")


def _norm(value: object) -> object:
    if isinstance(value, (tuple, list)):
        return [_norm(v) for v in value]
    return value


def _load(state: str) -> tuple[bytes, dict]:
    source = (_FIXTURES / state / "source.html").read_bytes()
    expected = json.loads((_FIXTURES / state / "expected.json").read_text(encoding="utf-8"))
    return source, expected


def _adapter(state: str) -> tuple[HtmlDocAdapter, dict]:
    source_bytes, expected = _load(state)
    url = expected["source_url"]
    policy = FetchPolicy(official_domains=("example.com",))
    fetcher = FixtureFetcher({url: (source_bytes, expected["mime"])}, policy)
    profile = resolve_profile(expected["profile"])
    return HtmlDocAdapter(fetcher, source_urls=(url,), profile=profile), expected


@pytest.mark.parametrize("state", _STATES)
def test_html_contract_matches_fixture(state: str) -> None:
    adapter, expected = _adapter(state)

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


def test_html_health_ok_offline() -> None:
    adapter, expected = _adapter("unchanged")
    health = adapter.health()
    assert isinstance(health, AdapterHealth)
    assert health.healthy is True
    assert health.source_url == expected["source_url"]


def test_html_health_unhealthy_when_source_unreachable() -> None:
    policy = FetchPolicy(official_domains=("example.com",))
    adapter = HtmlDocAdapter(
        FixtureFetcher({}, policy),
        source_urls=("https://example.com/limits",),
        profile=resolve_profile("quota_document"),
    )
    health = adapter.health()
    assert health.healthy is False
    assert "not_found" in health.detail


def test_html_malformed_missing_table_never_crashes_or_guesses() -> None:
    adapter, _ = _adapter("malformed")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    candidates = adapter.extract(document)
    assert len(candidates) == 1
    (candidate,) = candidates
    assert candidate.verification_state == "rejected"
    assert candidate.facts.get("error") == "table_not_found"
    assert not any(key in candidate.facts for key in _MATERIAL_KEYS)
    assert adapter.validate(candidate)  # captured validation failure


def test_html_garbage_bytes_never_crash() -> None:
    url = "https://example.com/limits"
    policy = FetchPolicy(official_domains=("example.com",))
    fetcher = FixtureFetcher({url: (b"\x00\x01\x02 not html <<<", "text/html")}, policy)
    adapter = HtmlDocAdapter(fetcher, source_urls=(url,), profile=resolve_profile("quota_document"))
    document = adapter.canonicalize(adapter.fetch(url))
    candidates = adapter.extract(document)  # must not raise
    assert len(candidates) == 1
    assert candidates[0].verification_state == "rejected"


def test_html_partial_missing_column_is_unknown_not_guessed() -> None:
    adapter, _ = _adapter("partial")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    (candidate,) = adapter.extract(document)
    # The "Offer type" column is absent from the table -> UNKNOWN, never guessed.
    assert candidate.facts["offer_type"] is None
    assert candidate.facts["has_paid_dependencies"] is None
    problems = adapter.validate(candidate)
    assert any("offer_type" in p for p in problems)


def test_html_contradictory_extracts_both_unresolved() -> None:
    adapter, expected = _adapter("contradictory")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    candidates = adapter.extract(document)
    assert len(candidates) == 2
    assert {c.facts["service"] for c in candidates} == {"Workers"}
    assert {c.facts["offer_type"] for c in candidates} == set(expected["contradiction"]["values"])


def test_html_non_allowlisted_url_is_refused() -> None:
    policy = FetchPolicy(official_domains=("example.com",))
    fetcher = FixtureFetcher({"https://evil.test/limits": (b"<html></html>", "text/html")}, policy)
    adapter = HtmlDocAdapter(
        fetcher,
        source_urls=("https://evil.test/limits",),
        profile=resolve_profile("quota_document"),
    )
    with pytest.raises(DisallowedHostError):
        adapter.fetch("https://evil.test/limits")


def test_html_uses_only_the_fetcher_seam(monkeypatch) -> None:
    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("adapter opened a socket directly")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    adapter, _ = _adapter("unchanged")
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    assert adapter.extract(document)


def test_resolve_profile_rejects_unknown_name() -> None:
    with pytest.raises(UnknownProfileError):
        resolve_profile("does_not_exist")
    with pytest.raises(UnknownProfileError):
        resolve_profile(None)


def test_build_adapter_resolves_html_type_with_profile() -> None:
    source = Source(
        adapter_type="html",
        trust_level="official",
        endpoint="https://example.com/limits",
        parser_profile="quota_document",
    )
    policy = FetchPolicy(official_domains=("example.com",))
    adapter = build_adapter(source, FixtureFetcher({}, policy))
    assert isinstance(adapter, HtmlDocAdapter)
    assert adapter.discover() == ("https://example.com/limits",)


def test_build_adapter_html_rejects_unknown_profile() -> None:
    source = Source(
        adapter_type="html",
        trust_level="official",
        endpoint="https://example.com/limits",
        parser_profile="bogus_profile",
    )
    policy = FetchPolicy(official_domains=("example.com",))
    with pytest.raises(UnknownProfileError):
        build_adapter(source, FixtureFetcher({}, policy))
