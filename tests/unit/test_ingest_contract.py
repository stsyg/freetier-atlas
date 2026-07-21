"""Contract tests for the source-adapter boundary (F004 slice 1).

Drives the reference structured/JSON adapter through all seven contract methods
against an offline :class:`FixtureFetcher`, and pins the two load-bearing
invariants: only official sources may produce evidence, and an adapter never
guesses malformed data. Also proves the ABC refuses an incomplete adapter.
"""

from __future__ import annotations

import pytest
from app.ingest import (
    AdapterHealth,
    CandidateFacts,
    DisallowedHostError,
    EvidenceLocation,
    FetchPolicy,
    FetchResult,
    FixtureFetcher,
    FixtureResponse,
    SourceAdapter,
    SourceDocument,
)
from app.ingest.reference import ReferenceJSONAdapter

ALLOWLIST = ("cloudflare.com",)
DOC_URL = "https://cloudflare.com/free.json"
GOOD_FACTS = (
    b'{"offer_type": "always_free", "requires_card": false, '
    b'"has_paid_dependencies": false, "exhaustion_behaviour": "hard_stop"}'
)


def make_adapter(*, trust_level: str, body: bytes = GOOD_FACTS) -> ReferenceJSONAdapter:
    fetcher = FixtureFetcher(
        {DOC_URL: FixtureResponse(body=body, content_type="application/json")},
        FetchPolicy(allowlist=ALLOWLIST),
    )
    return ReferenceJSONAdapter(
        source_id="cloudflare-free",
        trust_level=trust_level,
        allowlist=ALLOWLIST,
        fetcher=fetcher,
        document_url=DOC_URL,
    )


def run_pipeline(adapter: ReferenceJSONAdapter):
    (url,) = adapter.discover()
    result = adapter.fetch(url)
    document = adapter.canonicalize(result)
    candidate = adapter.validate(adapter.extract(document))
    return document, candidate


# --------------------------------------------------------------------------- #
# Full contract, official source
# --------------------------------------------------------------------------- #
def test_official_adapter_drives_full_contract():
    adapter = make_adapter(trust_level="official")

    urls = adapter.discover()
    assert urls == (DOC_URL,)

    result = adapter.fetch(urls[0])
    assert isinstance(result, FetchResult)

    document = adapter.canonicalize(result)
    assert isinstance(document, SourceDocument)
    assert document.content_hash == result.content_hash

    candidate = adapter.extract(document)
    assert isinstance(candidate, CandidateFacts)
    assert candidate.verification_state == "candidate"
    assert candidate.facts["offer_type"] == "always_free"
    assert candidate.facts["requires_card"] is False

    validated = adapter.validate(candidate)
    assert validated.facts == candidate.facts

    evidence = adapter.evidence(document, validated)
    assert evidence
    assert all(isinstance(e, EvidenceLocation) for e in evidence)
    assert all(e.content_hash == document.content_hash for e in evidence)

    health = adapter.health()
    assert isinstance(health, AdapterHealth)
    assert health.ok is True


# --------------------------------------------------------------------------- #
# Community / non-official sources cannot produce evidence
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("trust_level", ["community", "unknown"])
def test_non_official_source_cannot_produce_evidence(trust_level):
    adapter = make_adapter(trust_level=trust_level)
    assert adapter.is_official is False
    document, candidate = run_pipeline(adapter)
    with pytest.raises(PermissionError):
        adapter.evidence(document, candidate)


def test_official_source_is_recognised():
    assert make_adapter(trust_level="official").is_official is True


# --------------------------------------------------------------------------- #
# Adapter refuses a non-allowlisted URL via the shared fetcher
# --------------------------------------------------------------------------- #
def test_adapter_refuses_non_allowlisted_url():
    adapter = make_adapter(trust_level="official")
    with pytest.raises(DisallowedHostError):
        adapter.fetch("https://evil.example.com/free.json")


# --------------------------------------------------------------------------- #
# Malformed / partial data is handled, never guessed
# --------------------------------------------------------------------------- #
def test_malformed_json_is_rejected_not_guessed():
    adapter = make_adapter(trust_level="official", body=b"{not valid json")
    document, candidate = run_pipeline(adapter)
    assert candidate.verification_state == "rejected"
    assert candidate.facts == {}
    assert any("malformed" in w.lower() for w in candidate.warnings)
    # No facts -> no evidence, even for an official source.
    assert adapter.evidence(document, candidate) == ()


def test_partial_data_yields_warnings_and_omits_unknown_facts():
    body = b'{"offer_type": "always_free"}'
    adapter = make_adapter(trust_level="official", body=body)
    _document, candidate = run_pipeline(adapter)
    assert candidate.facts == {"offer_type": "always_free"}
    assert any("requires_card" in w for w in candidate.warnings)


def test_non_boolean_flag_is_dropped_with_warning():
    body = b'{"requires_card": "yes"}'
    adapter = make_adapter(trust_level="official", body=body)
    _document, candidate = run_pipeline(adapter)
    assert "requires_card" not in candidate.facts
    assert any("requires_card" in w for w in candidate.warnings)


# --------------------------------------------------------------------------- #
# Candidate facts carrier enforces the pre-publication invariant
# --------------------------------------------------------------------------- #
def test_candidate_facts_rejects_published_state():
    with pytest.raises(ValueError):
        CandidateFacts(
            source_id="s",
            trust_level="official",
            verification_state="verified",
            facts={},
            content_hash="abc",
        )


def test_candidate_facts_rejects_unknown_trust_level():
    with pytest.raises(ValueError):
        CandidateFacts(
            source_id="s",
            trust_level="bogus",
            verification_state="candidate",
            facts={},
            content_hash="abc",
        )


# --------------------------------------------------------------------------- #
# The ABC refuses an adapter missing a contract method
# --------------------------------------------------------------------------- #
def test_abc_rejects_adapter_missing_a_method():
    class IncompleteAdapter(SourceAdapter):
        source_type = "broken"

        def discover(self):  # pragma: no cover - never instantiated
            return ()

        def fetch(self, url):  # pragma: no cover
            raise NotImplementedError

        def canonicalize(self, result):  # pragma: no cover
            raise NotImplementedError

        def extract(self, document):  # pragma: no cover
            raise NotImplementedError

        def validate(self, candidate):  # pragma: no cover
            raise NotImplementedError

        # evidence() deliberately omitted.

        def health(self):  # pragma: no cover
            raise NotImplementedError

    with pytest.raises(TypeError):
        IncompleteAdapter(
            source_id="x",
            trust_level="official",
            allowlist=ALLOWLIST,
            fetcher=FixtureFetcher({}, FetchPolicy(allowlist=ALLOWLIST)),
        )


def test_complete_adapter_instantiates():
    adapter = make_adapter(trust_level="official")
    assert isinstance(adapter, SourceAdapter)
    assert adapter.source_type == "structured"
