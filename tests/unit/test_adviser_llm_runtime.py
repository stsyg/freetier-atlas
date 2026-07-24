"""Unit tests for the LLM runtime wiring: fail-safe config + registry build.

Proves the adviser degrades to a deterministic-only posture when no/invalid LLM
config is present, that providers are disabled by default (the shipped example
config registers nothing), that only recognised enabled providers register with
the correct tier/consent policy, and that the base_url egress guard behaves.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.adviser.llm.adapters import GeminiAdapter, OllamaAdapter
from app.adviser.llm.guards import ProviderBaseUrlError, guard_base_url
from app.adviser.llm.protocol import ProviderTier
from app.adviser.llm.runtime import (
    DEFAULT_LIMITS,
    build_registry,
    get_limits,
    get_llm_section,
    reset_cache,
)
from app.config.loader import load_and_validate
from app.config.models import LlmProvider, LlmSection, PublicAdviserLimits

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "config" / "examples" / "llm-providers.example.yaml"


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


def _limits() -> PublicAdviserLimits:
    return DEFAULT_LIMITS


def _section(providers: dict[str, LlmProvider]) -> LlmSection:
    return LlmSection(mode="hybrid", public_adviser=_limits(), providers=providers)


def test_no_config_path_is_deterministic_only(monkeypatch) -> None:
    monkeypatch.setattr("app.adviser.llm.runtime.get_settings", lambda: _Settings(None))
    reset_cache()
    assert get_llm_section() is None
    assert get_limits() is DEFAULT_LIMITS


def test_missing_file_degrades_safely(monkeypatch, tmp_path) -> None:
    missing = tmp_path / "nope.yaml"
    monkeypatch.setattr("app.adviser.llm.runtime.get_settings", lambda: _Settings(str(missing)))
    reset_cache()
    assert get_llm_section() is None  # no crash


def test_invalid_family_degrades_safely(monkeypatch, tmp_path) -> None:
    bad = tmp_path / "schedules.yaml"
    bad.write_text("schedules:\n  rss: '* * * * *'\n", encoding="utf-8")
    monkeypatch.setattr("app.adviser.llm.runtime.get_settings", lambda: _Settings(str(bad)))
    reset_cache()
    # Not an llm-providers file -> deterministic-only, no exception.
    assert get_llm_section() is None


def test_example_config_registers_no_providers() -> None:
    # Every provider in the shipped example is disabled -> empty registry.
    section = load_and_validate(_EXAMPLE).llm  # type: ignore[attr-defined]
    assert build_registry(section) == ()


def test_enabled_local_provider_registers_without_consent() -> None:
    section = _section({"ollama": LlmProvider(enabled=True, model="qwen3.5:9b")})
    registry = build_registry(section)
    assert len(registry) == 1
    rp = registry[0]
    assert rp.name == "ollama"
    assert rp.tier is ProviderTier.LOCAL
    assert rp.consent_required is False
    assert isinstance(rp.provider, OllamaAdapter)


def test_enabled_external_provider_requires_consent() -> None:
    section = _section({"gemini": LlmProvider(enabled=True, api_key_env="GEMINI_API_KEY")})
    registry = build_registry(section)
    assert len(registry) == 1
    assert registry[0].tier is ProviderTier.FREE_HOSTED
    assert registry[0].consent_required is True
    assert isinstance(registry[0].provider, GeminiAdapter)


def test_unknown_enabled_provider_is_ignored() -> None:
    section = _section({"mystery": LlmProvider(enabled=True)})
    assert build_registry(section) == ()


def test_disabled_provider_not_registered() -> None:
    section = _section({"ollama": LlmProvider(enabled=False, model="x")})
    assert build_registry(section) == ()


def test_none_section_is_empty_registry() -> None:
    assert build_registry(None) == ()


def test_guard_base_url_accepts_http_and_https() -> None:
    assert guard_base_url("https://api.example.com/v1") == "https://api.example.com/v1"
    assert guard_base_url("http://localhost:11434") == "http://localhost:11434"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///etc/passwd",
        "ftp://example.com",
        "https://",  # no host
        "https://user:pass@example.com",  # embedded credentials  # pragma: allowlist secret
    ],
)
def test_guard_base_url_rejects_unsafe(url: str) -> None:
    with pytest.raises(ProviderBaseUrlError):
        guard_base_url(url)


class _Settings:
    def __init__(self, path: str | None) -> None:
        self.llm_config_path = path
