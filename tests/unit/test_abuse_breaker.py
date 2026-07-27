"""Unit tests for the per-provider circuit breaker."""

from __future__ import annotations

from datetime import timedelta

import pytest
from app.adviser.abuse.breaker import (
    STATE_CLOSED,
    STATE_OPEN,
    BreakerProvider,
    CircuitOpenError,
    wrap_registry,
)
from app.adviser.abuse.store import InMemoryAbuseStore
from app.adviser.llm.fake import FakeInterpreter
from app.adviser.llm.protocol import LlmProviderError, ProviderTier
from app.adviser.llm.routing import RegisteredProvider

from tests.support.abuse import FIXED_NOW, make_config

_CANDIDATE = {
    "requirements": [
        {
            "category": "object-file-storage",
            "demands": [{"metric": "storage", "amount": "5", "unit": "GB"}],
        }
    ]
}


class _Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


def test_breaker_opens_after_threshold_and_short_circuits() -> None:
    store = InMemoryAbuseStore()
    config = make_config(breaker_threshold=3, breaker_cooldown_seconds=30)
    clock = _Clock(FIXED_NOW)
    failing = FakeInterpreter(raise_error=True)
    breaker = BreakerProvider("ollama", failing, store, config, clock)

    # Three consecutive failures open the breaker (each surfaces the original error).
    for _ in range(3):
        with pytest.raises(LlmProviderError):
            breaker.interpret("desc", None)
    assert failing.calls == 3
    assert store.breaker_load("ollama").state == STATE_OPEN

    # While open (within cooldown) the inner provider is NOT called.
    with pytest.raises(CircuitOpenError):
        breaker.interpret("desc", None)
    assert failing.calls == 3


def test_breaker_half_open_probe_closes_on_success() -> None:
    store = InMemoryAbuseStore()
    config = make_config(breaker_threshold=2, breaker_cooldown_seconds=30)
    clock = _Clock(FIXED_NOW)
    provider = FakeInterpreter(raise_error=True)
    breaker = BreakerProvider("ollama", provider, store, config, clock)

    for _ in range(2):
        with pytest.raises(LlmProviderError):
            breaker.interpret("desc", None)
    assert store.breaker_load("ollama").state == STATE_OPEN

    # After the cooldown a half-open probe is allowed; make it succeed.
    clock.now = FIXED_NOW + timedelta(seconds=31)
    provider._raise_error = False  # type: ignore[attr-defined]
    provider._candidate = _CANDIDATE  # type: ignore[attr-defined]
    result = breaker.interpret("desc", None)
    assert result == _CANDIDATE
    assert store.breaker_load("ollama").state == STATE_CLOSED


def test_breaker_half_open_probe_reopens_on_failure() -> None:
    store = InMemoryAbuseStore()
    config = make_config(breaker_threshold=2, breaker_cooldown_seconds=30)
    clock = _Clock(FIXED_NOW)
    provider = FakeInterpreter(raise_error=True)
    breaker = BreakerProvider("ollama", provider, store, config, clock)

    for _ in range(2):
        with pytest.raises(LlmProviderError):
            breaker.interpret("desc", None)
    opened_first = store.breaker_load("ollama").opened_at

    clock.now = FIXED_NOW + timedelta(seconds=31)
    with pytest.raises(LlmProviderError):
        breaker.interpret("desc", None)  # half-open probe fails -> re-open
    record = store.breaker_load("ollama")
    assert record.state == STATE_OPEN
    assert record.opened_at == clock.now != opened_first


def test_wrap_registry_preserves_identity_fields() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    rp = RegisteredProvider("gemini", ProviderTier.FREE_HOSTED, FakeInterpreter(), True)
    (wrapped,) = wrap_registry((rp,), store, config)
    assert wrapped.name == "gemini"
    assert wrapped.tier == ProviderTier.FREE_HOSTED
    assert wrapped.consent_required is True
    assert isinstance(wrapped.provider, BreakerProvider)
