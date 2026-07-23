"""Contract tests for the Cloudflare OFFICIAL free-tier HTML profiles (F005).

Offline, fixture-driven proof that the generic HTML table-walking adapter,
driven by the provider-specific ``cloudflare_workers_limits`` /
``cloudflare_pages_limits`` extraction profiles (data in the registry, not
code), extracts the REAL captured Cloudflare free-tier facts deterministically:
the same fixture always yields identical CandidateFacts and an identical content
hash, and a column that is absent yields UNKNOWN (``None``) -- never a guessed
number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.ingest import (
    CandidateFacts,
    EvidenceLocation,
    FetchPolicy,
    FixtureFetcher,
    HtmlDocAdapter,
    resolve_profile,
)
from app.ingest.scan import _candidate_key, _content_hash

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingest" / "cloudflare" / "html"
_STATES = ("cloudflare-workers-limits", "cloudflare-pages-limits")
_OFFICIAL_DOMAINS = ("cloudflare.com", "developers.cloudflare.com")


def _load(state: str) -> tuple[bytes, dict]:
    source = (_FIXTURES / state / "source.html").read_bytes()
    expected = json.loads((_FIXTURES / state / "expected.json").read_text(encoding="utf-8"))
    return source, expected


def _adapter(state: str, body: bytes | None = None) -> tuple[HtmlDocAdapter, dict]:
    source_bytes, expected = _load(state)
    url = expected["source_url"]
    policy = FetchPolicy(official_domains=_OFFICIAL_DOMAINS)
    fetcher = FixtureFetcher(
        {url: (body if body is not None else source_bytes, expected["mime"])}, policy
    )
    profile = resolve_profile(expected["profile"])
    return HtmlDocAdapter(fetcher, source_urls=(url,), profile=profile), expected


@pytest.mark.parametrize("state", _STATES)
def test_cloudflare_extraction_matches_fixture(state: str) -> None:
    adapter, expected = _adapter(state)

    assert adapter.discover() == (expected["source_url"],)
    document = adapter.canonicalize(adapter.fetch(expected["source_url"]))
    candidates = list(adapter.extract(document))
    assert len(candidates) == expected["candidate_count"]

    candidate = candidates[0]
    want = expected["candidates"][0]
    assert isinstance(candidate, CandidateFacts)
    assert candidate.verification_state == want["verification_state"]
    assert candidate.verification_state != "verified"

    # Every captured official fact is extracted verbatim -- including quota
    # numbers with thousands separators (proves no list-coercion comma split).
    for key, value in want["facts"].items():
        assert candidate.facts.get(key) == value, key

    assert adapter.validate(candidate) == []

    evidence = adapter.evidence(candidate)
    assert evidence and isinstance(evidence[0], EvidenceLocation)
    assert evidence[0].url == want["evidence_url"]
    assert evidence[0].selector == want["evidence_selector"]
    assert evidence[0].content_hash == document.content_hash


@pytest.mark.parametrize("state", _STATES)
def test_cloudflare_extraction_is_deterministic(state: str) -> None:
    adapter, expected = _adapter(state)
    url = expected["source_url"]

    first = adapter.extract(adapter.canonicalize(adapter.fetch(url)))[0]
    second = adapter.extract(adapter.canonicalize(adapter.fetch(url)))[0]

    assert first.facts == second.facts
    assert _content_hash(first.facts) == _content_hash(second.facts)
    assert _candidate_key(first) == _candidate_key(second)


@pytest.mark.parametrize("state", _STATES)
def test_cloudflare_missing_column_is_unknown_not_guessed(state: str) -> None:
    # Drop the "Memory"/"File size" data cell + header so a mapped column is
    # absent; the adapter must record UNKNOWN (None), never invent a value.
    source_bytes, expected = _load(state)
    text = source_bytes.decode("utf-8")
    if state == "cloudflare-workers-limits":
        header, cell, field_name = "<th>Memory</th>", "<td>128 MB</td>", "memory"
    else:
        header, cell, field_name = "<th>File size</th>", "<td>25 MiB</td>", "file_size"
    mutated = text.replace(header, "").replace(cell, "").encode("utf-8")

    adapter, _ = _adapter(state, body=mutated)
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(expected["source_url"])))
    assert candidate.facts[field_name] is None
    # The required identity fields are still present, so the candidate is valid.
    assert candidate.facts["service"] == expected["candidates"][0]["facts"]["service"]
    assert adapter.validate(candidate) == []


def test_cloudflare_profiles_registered() -> None:
    for name in ("cloudflare_workers_limits", "cloudflare_pages_limits"):
        profile = resolve_profile(name)
        assert profile.name == name
        assert "service" in profile.columns
        assert profile.required_fields == ("service", "offer_type")
