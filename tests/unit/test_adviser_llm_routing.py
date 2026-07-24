"""Unit tests for the adviser routing ladder (F007 slice 1).

Proves: deterministic-parser-first precedence; local vs consent-gated external
tiers; and that a timeout / provider error / consent-absent / schema-rejected
candidate each degrade to the next tier and ultimately to the deterministic
fallback -- never a hard failure. The fake interpreter is the only "LLM" here;
no network is ever touched.
"""

from __future__ import annotations

from app.adviser.llm.fake import FakeInterpreter
from app.adviser.llm.protocol import ProviderTier
from app.adviser.llm.routing import (
    REASON_CONSENT_NOT_GRANTED,
    REASON_DETERMINISTIC_PARSER,
    REASON_INVALID_INTERPRETATION,
    REASON_NO_PROVIDER_ENABLED,
    RegisteredProvider,
    route,
)

_VALID_CANDIDATE = {
    "requirements": [
        {
            "category": "object-file-storage",
            "demands": [{"metric": "storage", "amount": "3", "unit": "GB"}],
        }
    ]
}
_PARSEABLE = "object storage with 10 GB storage"
_UNPARSEABLE = "please help me with my project somehow"


def _provider(name, tier, *, consent_required, **kw) -> RegisteredProvider:
    return RegisteredProvider(name, tier, FakeInterpreter(**kw), consent_required)


def test_deterministic_parser_wins_before_any_llm() -> None:
    # A provider that would raise proves the LLM is never consulted when the
    # deterministic parser already succeeds.
    llm = _provider("local", ProviderTier.LOCAL, consent_required=False, raise_error=True)
    outcome = route(_PARSEABLE, None, [llm])
    assert outcome.llm_used is False
    assert outcome.tier == "deterministic_parser"
    assert outcome.fallback_reason == REASON_DETERMINISTIC_PARSER
    assert outcome.interpreted is True
    assert llm.provider.calls == 0


def test_local_llm_used_when_parser_cannot() -> None:
    llm = _provider(
        "ollama", ProviderTier.LOCAL, consent_required=False, candidate=_VALID_CANDIDATE
    )
    outcome = route(_UNPARSEABLE, None, [llm])
    assert outcome.llm_used is True
    assert outcome.provider == "ollama"
    assert outcome.tier == "local"
    assert outcome.external_processing_used is False
    assert outcome.fallback_reason is None


def test_external_provider_skipped_without_consent() -> None:
    ext = _provider(
        "gemini", ProviderTier.FREE_HOSTED, consent_required=True, candidate=_VALID_CANDIDATE
    )
    outcome = route(_UNPARSEABLE, None, [ext], external_processing_consented=False)
    assert outcome.llm_used is False
    assert outcome.tier == "deterministic_fallback"
    assert outcome.fallback_reason == REASON_CONSENT_NOT_GRANTED
    assert ext.provider.calls == 0  # never invoked without consent


def test_external_provider_used_with_consent() -> None:
    ext = _provider(
        "gemini", ProviderTier.FREE_HOSTED, consent_required=True, candidate=_VALID_CANDIDATE
    )
    outcome = route(_UNPARSEABLE, None, [ext], external_processing_consented=True)
    assert outcome.llm_used is True
    assert outcome.provider == "gemini"
    assert outcome.external_processing_used is True


def test_timeout_degrades_to_fallback() -> None:
    llm = _provider("ollama", ProviderTier.LOCAL, consent_required=False, raise_timeout=True)
    outcome = route(_UNPARSEABLE, None, [llm])
    assert outcome.llm_used is False
    assert outcome.tier == "deterministic_fallback"
    assert outcome.fallback_reason == "provider_timeout"


def test_provider_error_degrades_to_fallback() -> None:
    llm = _provider("ollama", ProviderTier.LOCAL, consent_required=False, raise_error=True)
    outcome = route(_UNPARSEABLE, None, [llm])
    assert outcome.fallback_reason == "provider_error"
    assert outcome.interpreted is False


def test_malformed_llm_output_rejected_by_schema_then_fallback() -> None:
    # A candidate with a non-canonical category / unknown field must be rejected
    # by the strict schema and degrade -- the LLM cannot smuggle bad structure in.
    bad = {"requirements": [{"category": "not-a-real-category", "demands": []}]}
    llm = _provider("ollama", ProviderTier.LOCAL, consent_required=False, candidate=bad)
    outcome = route(_UNPARSEABLE, None, [llm])
    assert outcome.llm_used is False
    assert outcome.fallback_reason == REASON_INVALID_INTERPRETATION


def test_url_smuggled_by_llm_is_rejected() -> None:
    # A candidate whose field carries a URL marker is rejected by _reject_url_like.
    bad = {
        "workload_name": "http://evil.example.com",
        "requirements": _VALID_CANDIDATE["requirements"],
    }
    llm = _provider("ollama", ProviderTier.LOCAL, consent_required=False, candidate=bad)
    outcome = route(_UNPARSEABLE, None, [llm])
    assert outcome.llm_used is False
    assert outcome.fallback_reason == REASON_INVALID_INTERPRETATION


def test_tier_precedence_local_before_external() -> None:
    local = _provider(
        "ollama", ProviderTier.LOCAL, consent_required=False, candidate=_VALID_CANDIDATE
    )
    external = _provider(
        "gemini", ProviderTier.FREE_HOSTED, consent_required=True, candidate=_VALID_CANDIDATE
    )
    outcome = route(_UNPARSEABLE, None, [external, local], external_processing_consented=True)
    # Local is attempted first regardless of registration order.
    assert outcome.provider == "ollama"
    assert external.provider.calls == 0


def test_empty_registry_yields_no_provider_enabled() -> None:
    outcome = route(_UNPARSEABLE, None, [])
    assert outcome.llm_used is False
    assert outcome.fallback_reason == REASON_NO_PROVIDER_ENABLED
    assert outcome.routing_path == ("deterministic_parser", "deterministic_fallback")
