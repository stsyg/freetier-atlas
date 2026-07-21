"""Source-adapter contract tests (offline).

Verifies the ABC enforces all seven methods, that the reference JSON adapter
runs end-to-end through the offline FixtureFetcher producing candidate-only
facts with evidence and health, and that the verification vocabulary is closed
and matches docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import pytest
from app.ingest import (
    AdapterHealth,
    CandidateFacts,
    EvidenceLocation,
    FetchPolicy,
    FixtureFetcher,
    JsonOfferAdapter,
    OfflineFetcher,
    SourceAdapter,
    SourceDocument,
    is_verification_state,
)
from app.ingest.base import CONTRACT_METHODS
from app.ingest.vocab import VERIFICATION_STATES

_DOCUMENTED_STATES = {
    "detected",
    "extracting",
    "candidate",
    "verified",
    "verified_with_caveats",
    "conflict",
    "stale",
    "withdrawn",
    "rejected",
}


def _full_method_impls() -> dict:
    return {
        "discover": lambda self: [],
        "fetch": lambda self, url: None,
        "canonicalize": lambda self, result: None,
        "extract": lambda self, document: [],
        "validate": lambda self, candidate: [],
        "evidence": lambda self, candidate: [],
        "health": lambda self: None,
    }


def test_contract_method_names_are_the_seven() -> None:
    assert set(CONTRACT_METHODS) == {
        "discover",
        "fetch",
        "canonicalize",
        "extract",
        "validate",
        "evidence",
        "health",
    }


def test_complete_adapter_can_be_instantiated() -> None:
    cls = type("CompleteAdapter", (SourceAdapter,), _full_method_impls())
    instance = cls(OfflineFetcher())
    assert isinstance(instance, SourceAdapter)


@pytest.mark.parametrize("missing", CONTRACT_METHODS)
def test_adapter_missing_any_method_cannot_instantiate(missing) -> None:
    impls = _full_method_impls()
    del impls[missing]
    cls = type(f"Missing_{missing}", (SourceAdapter,), impls)
    with pytest.raises(TypeError):
        cls(OfflineFetcher())


def test_verification_vocab_is_closed_and_matches_docs() -> None:
    assert set(VERIFICATION_STATES) == _DOCUMENTED_STATES
    # No duplicates and deterministic ordering.
    assert len(VERIFICATION_STATES) == len(_DOCUMENTED_STATES)
    assert is_verification_state("candidate")
    assert not is_verification_state("published")


def test_candidate_facts_cannot_be_born_verified() -> None:
    with pytest.raises(ValueError):
        CandidateFacts(
            provider="p", source_url="https://p.example/x", facts={}, verification_state="verified"
        )
    with pytest.raises(ValueError):
        CandidateFacts(
            provider="p", source_url="https://p.example/x", facts={}, verification_state="bogus"
        )
    # candidate is allowed.
    ok = CandidateFacts(provider="p", source_url="https://p.example/x", facts={})
    assert ok.verification_state == "candidate"


# --------------------------------------------------------------------------
# Reference adapter end-to-end (offline)
# --------------------------------------------------------------------------

_SOURCE_URL = "https://provider.example/free.json"
_DOC = (
    b'{"provider":"provider.example","offers":['
    b'{"service":"Widgets","offer_type":"always_free",'
    b'"requires_card":false,"has_paid_dependencies":false,'
    b'"quotas":[{"metric":"requests","exhaustion_behaviour":"hard_stop"}]}'
    b"]}"
)


def _reference_adapter() -> JsonOfferAdapter:
    policy = FetchPolicy(official_domains=("provider.example",))
    fetcher = FixtureFetcher({_SOURCE_URL: (_DOC, "application/json")}, policy)
    return JsonOfferAdapter(fetcher, source_urls=(_SOURCE_URL,))


def test_reference_adapter_end_to_end_offline() -> None:
    adapter = _reference_adapter()

    urls = adapter.discover()
    assert urls == (_SOURCE_URL,)

    result = adapter.fetch(urls[0])
    document = adapter.canonicalize(result)
    assert isinstance(document, SourceDocument)
    assert document.mime == "application/json"
    assert document.content_hash == result.content_hash

    candidates = adapter.extract(document)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert isinstance(candidate, CandidateFacts)
    # Candidate only -- never a verified fact.
    assert candidate.verification_state == "candidate"
    assert candidate.facts["service"] == "Widgets"
    assert candidate.facts["offer_type"] == "always_free"
    assert candidate.facts["quotas"] == ("hard_stop",)

    assert adapter.validate(candidate) == []

    evidence = adapter.evidence(candidate)
    assert evidence and isinstance(evidence[0], EvidenceLocation)
    assert evidence[0].url == _SOURCE_URL
    assert evidence[0].selector == "$.offers[0]"
    assert evidence[0].content_hash == document.content_hash


def test_reference_adapter_validate_flags_missing_fields() -> None:
    adapter = _reference_adapter()
    incomplete = CandidateFacts(
        provider="p", source_url=_SOURCE_URL, facts={"service": None, "offer_type": None}
    )
    problems = adapter.validate(incomplete)
    assert any("service" in p for p in problems)
    assert any("offer_type" in p for p in problems)


def test_reference_adapter_health_ok_offline() -> None:
    adapter = _reference_adapter()
    health = adapter.health()
    assert isinstance(health, AdapterHealth)
    assert health.healthy is True
    assert health.source_url == _SOURCE_URL


def test_reference_adapter_health_unhealthy_when_source_unreachable() -> None:
    # FixtureFetcher with no matching fixture -> fetch raises -> health False.
    policy = FetchPolicy(official_domains=("provider.example",))
    adapter = JsonOfferAdapter(FixtureFetcher({}, policy), source_urls=(_SOURCE_URL,))
    health = adapter.health()
    assert health.healthy is False
    assert "not_found" in health.detail


def test_reference_adapter_uses_only_the_fetcher_seam(monkeypatch) -> None:
    # Prove the adapter reaches the network only through the injected fetcher:
    # a fetcher whose fetch is intercepted is the only I/O path used.
    import socket

    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("adapter opened a socket directly")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    adapter = _reference_adapter()
    # Full pipeline runs with sockets forbidden.
    document = adapter.canonicalize(adapter.fetch(adapter.discover()[0]))
    assert adapter.extract(document)
