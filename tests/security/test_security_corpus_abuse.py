"""S2 -- abuse controls: hashed-IP, kill switch, breaker, dedupe, proof-of-work.

Consolidates and extends the F007 slice-2 guarantees (docs/SECURITY_PRIVACY_ABUSE
+ owner decisions Q3/Q6/Q9): stdlib-only, no Redis, no external CAPTCHA, and
**no raw client IP or request body is ever persisted** -- only keyed HMAC
digests. Every check runs offline against the in-memory store / pure helpers or
the FastAPI app with an injected fake provider.

Adversarial coverage:

* deterministic overage -> hard 429 with ``Retry-After``;
* only hashed IPs / body digests are ever stored (raw IP never appears);
* the AI kill switch degrades the assisted path to deterministic (200,
  ``llm_used`` false) without hard-failing, and ``/recommend`` stays available;
* the circuit breaker opens after N failures, adds **zero** extra provider calls
  while open, and half-open closes on success / re-opens on failure;
* identical requests are collapsed (the provider is called once);
* the proof-of-work token is server-signed (stdlib HMAC), single-use, expiring,
  IP-bound, constant-time verified, and every forged / replayed / expired /
  downgraded / malformed / insufficient-work token is rejected.
"""

from __future__ import annotations

import hashlib
import inspect
import re
from datetime import timedelta

import pytest
from app.adviser.abuse import (
    InMemoryAbuseStore,
    enforce_deterministic,
    evaluate_assisted,
    hashing,
)
from app.adviser.abuse.breaker import BreakerProvider, CircuitOpenError, wrap_registry
from app.adviser.abuse.pow import issue_challenge, solve, verify_solution
from app.adviser.abuse.service import (
    AI_KILL_SWITCH_FLAG,
    REASON_AI_KILL_SWITCH,
    REASON_DEDUPLICATED,
    SCOPE_DETERMINISTIC,
)
from app.adviser.llm.protocol import LlmError, LlmProviderError, ProviderTier
from app.adviser.llm.routing import RegisteredProvider
from app.adviser.llm.runtime import DEFAULT_LIMITS
from app.db import get_session
from app.main import app
from fastapi.testclient import TestClient

from tests.support.abuse import FIXED_NOW, make_config, make_limits, make_request
from tests.unit.test_adviser_router import _pool

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RAW_IP = "203.0.113.5"
_DOTTED_QUAD = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

_VALID_CANDIDATE = {
    "requirements": [
        {
            "category": "object-file-storage",
            "demands": [{"metric": "storage", "amount": "5", "unit": "GB"}],
        }
    ]
}
_UNPARSEABLE = "please help me choose something for my little project"

_AI_LIMITS = DEFAULT_LIMITS.model_copy(
    update={"ai_requests_per_ip_per_day": 3, "require_captcha": False}
)


def _body(marker: str):
    """A distinct RecommendationRequest-shaped model (unique dedupe key)."""

    from app.adviser.schema import RecommendationRequest

    return RecommendationRequest.model_validate(
        {
            "workload_name": marker,
            "requirements": [
                {
                    "category": "object-file-storage",
                    "demands": [{"metric": "storage", "amount": "5", "unit": "GB"}],
                }
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Deterministic overage -> hard 429 with Retry-After.                         #
# --------------------------------------------------------------------------- #


def test_deterministic_overage_returns_not_allowed_with_retry_after() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    request = make_request(ip=_RAW_IP)
    limit = 2
    decisions = [
        enforce_deterministic(
            store, config, request, _body(f"r{i}"), SCOPE_DETERMINISTIC, limit, FIXED_NOW
        )
        for i in range(limit + 1)
    ]
    assert [d.allowed for d in decisions] == [True, True, False]
    assert decisions[-1].retry_after >= 1


def test_recommend_endpoint_emits_429_and_retry_after_header(monkeypatch) -> None:
    store = InMemoryAbuseStore()
    zero = DEFAULT_LIMITS.model_copy(update={"deterministic_requests_per_ip_per_day": 0})
    monkeypatch.setattr("app.adviser.router.gather_candidates", lambda _s: _pool())
    monkeypatch.setattr("app.adviser.router.get_limits", lambda: zero)
    monkeypatch.setattr("app.adviser.router.get_registry", lambda: ())
    monkeypatch.setattr("app.adviser.router.get_abuse_store", lambda: store)
    app.dependency_overrides[get_session] = lambda: None
    try:
        c = TestClient(app)
        r = c.post("/adviser/recommend", json=_VALID_CANDIDATE)
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


# --------------------------------------------------------------------------- #
# Only hashed IPs / body digests are ever persisted (raw IP never stored).    #
# --------------------------------------------------------------------------- #


def test_only_hashed_ip_and_body_digests_are_persisted() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    request = make_request(ip=_RAW_IP)
    # Exercise every persistence path: rate + dedupe + proof-of-work issuance.
    enforce_deterministic(store, config, request, _body("a"), SCOPE_DETERMINISTIC, 10, FIXED_NOW)
    ip_hash = hashing.hash_ip(config.secret, _RAW_IP)
    issue_challenge(store, config, ip_hash, FIXED_NOW)

    # Rate keys: the IP component is a 64-hex digest, never a dotted-quad.
    for stored_ip, _scope, _window in store._rate:
        assert _HEX64.match(stored_ip), stored_ip
        assert not _DOTTED_QUAD.search(stored_ip)
    # Dedupe keys are opaque hex digests, not the raw body.
    for key in store._dedupe:
        assert _HEX64.match(key), key
    # Proof-of-work entries bind the hashed IP only.
    for _difficulty, stored_ip, *_rest in store._pow.values():
        assert _HEX64.match(stored_ip), stored_ip

    # A total scan: the raw IP appears nowhere in any persisted key or value.
    haystack = repr((store._rate, store._dedupe, store._pow, store._flags, store._breakers))
    assert _RAW_IP not in haystack
    assert not _DOTTED_QUAD.search(haystack)


# --------------------------------------------------------------------------- #
# AI kill switch: assisted degrades (no hard-fail); /recommend stays up.      #
# --------------------------------------------------------------------------- #


def test_kill_switch_forces_deterministic_in_service_layer() -> None:
    store = InMemoryAbuseStore()
    store.set_flag(AI_KILL_SWITCH_FLAG, True, FIXED_NOW)
    config = make_config()
    decision = evaluate_assisted(
        store,
        config,
        make_limits(ai_requests_per_ip_per_day=5),
        make_request(),
        _body("k"),
        had_providers=True,
        pow_token=None,
        pow_nonce=None,
        now=FIXED_NOW,
    )
    assert decision.allow_ai is False
    assert decision.forced_reason == REASON_AI_KILL_SWITCH
    assert decision.rate_limited is False  # degrade, never hard-fail


def _assisted_client(monkeypatch, store, registry):
    monkeypatch.setattr("app.adviser.router.gather_candidates", lambda _s: _pool())
    monkeypatch.setattr("app.adviser.router.get_limits", lambda: _AI_LIMITS)
    monkeypatch.setattr("app.adviser.router.get_registry", lambda: registry)
    monkeypatch.setattr("app.adviser.router.get_abuse_store", lambda: store)
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


class _CountingProvider:
    """A deterministic provider double that counts and can flip failure mode."""

    def __init__(self, *, fail: bool, candidate=None) -> None:
        self.calls = 0
        self.fail = fail
        self._candidate = candidate or _VALID_CANDIDATE

    def interpret(self, description: str, limits=None) -> dict[str, object]:
        self.calls += 1
        if self.fail:
            raise LlmProviderError("provider down")
        return self._candidate


def test_kill_switch_degrades_assisted_endpoint_without_hard_fail(monkeypatch) -> None:
    store = InMemoryAbuseStore()
    store.set_flag(AI_KILL_SWITCH_FLAG, True, FIXED_NOW)
    provider = _CountingProvider(fail=False)
    registry = (RegisteredProvider("ollama", ProviderTier.LOCAL, provider, False),)
    try:
        c = _assisted_client(monkeypatch, store, registry)
        r = c.post("/adviser/recommend/assisted", json={"description": _UNPARSEABLE})
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert r.status_code == 200
    assert r.json()["routing"]["llm_used"] is False
    assert provider.calls == 0  # the LLM was never called


def test_recommend_stays_available_while_kill_switch_on(monkeypatch) -> None:
    store = InMemoryAbuseStore()
    store.set_flag(AI_KILL_SWITCH_FLAG, True, FIXED_NOW)
    try:
        c = _assisted_client(monkeypatch, store, ())
        r = c.post("/adviser/recommend", json=_VALID_CANDIDATE)
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Circuit breaker: opens after N failures, ZERO extra calls, half-open cycle. #
# --------------------------------------------------------------------------- #


def test_breaker_opens_and_adds_zero_extra_provider_calls() -> None:
    store = InMemoryAbuseStore()
    config = make_config(breaker_threshold=3, breaker_cooldown_seconds=30)
    clock = {"now": FIXED_NOW}
    inner = _CountingProvider(fail=True)
    breaker = BreakerProvider("gemini", inner, store, config, lambda: clock["now"])

    for _ in range(3):
        with pytest.raises(LlmError):
            breaker.interpret("x", None)
    assert inner.calls == 3  # breaker now OPEN

    # While open, the provider is skipped entirely (no extra inner call).
    with pytest.raises(CircuitOpenError):
        breaker.interpret("x", None)
    assert inner.calls == 3

    # Cooldown elapses -> a half-open probe is allowed and succeeds -> closes.
    clock["now"] = FIXED_NOW + timedelta(seconds=31)
    inner.fail = False
    assert breaker.interpret("x", None) == _VALID_CANDIDATE
    assert inner.calls == 4

    # Closed again: failures re-accumulate and re-open after the threshold.
    inner.fail = True
    for _ in range(3):
        with pytest.raises(LlmError):
            breaker.interpret("x", None)
    with pytest.raises(CircuitOpenError):
        breaker.interpret("x", None)
    assert inner.calls == 7  # 4 + 3 failures; the final open call did not run


def test_breaker_half_open_probe_failure_reopens() -> None:
    store = InMemoryAbuseStore()
    config = make_config(breaker_threshold=2, breaker_cooldown_seconds=30)
    clock = {"now": FIXED_NOW}
    inner = _CountingProvider(fail=True)
    breaker = BreakerProvider("gemini", inner, store, config, lambda: clock["now"])

    for _ in range(2):
        with pytest.raises(LlmError):
            breaker.interpret("x", None)
    # Open; cooldown elapses; the half-open probe fails -> re-opens immediately.
    clock["now"] = FIXED_NOW + timedelta(seconds=31)
    with pytest.raises(LlmError):
        breaker.interpret("x", None)
    calls_after_probe = inner.calls
    with pytest.raises(CircuitOpenError):
        breaker.interpret("x", None)
    assert inner.calls == calls_after_probe  # re-opened, no extra call


def test_wrap_registry_preserves_tier_and_consent() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    original = (
        RegisteredProvider("gemini", ProviderTier.FREE_HOSTED, _CountingProvider(fail=True), True),
    )
    wrapped = wrap_registry(original, store, config)
    assert wrapped[0].name == "gemini"
    assert wrapped[0].tier == ProviderTier.FREE_HOSTED
    assert wrapped[0].consent_required is True
    assert isinstance(wrapped[0].provider, BreakerProvider)


# --------------------------------------------------------------------------- #
# Dedupe collapses identical requests (the provider is called once).          #
# --------------------------------------------------------------------------- #


def test_identical_assisted_requests_are_deduplicated_in_service() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    body = _body("same")
    first = evaluate_assisted(
        store,
        config,
        make_limits(ai_requests_per_ip_per_day=5),
        make_request(),
        body,
        had_providers=True,
        pow_token=None,
        pow_nonce=None,
        now=FIXED_NOW,
    )
    second = evaluate_assisted(
        store,
        config,
        make_limits(ai_requests_per_ip_per_day=5),
        make_request(),
        body,
        had_providers=True,
        pow_token=None,
        pow_nonce=None,
        now=FIXED_NOW,
    )
    assert first.allow_ai is True
    assert second.deduplicated is True
    assert second.forced_reason == REASON_DEDUPLICATED


def test_duplicate_assisted_http_requests_call_provider_once(monkeypatch) -> None:
    store = InMemoryAbuseStore()
    provider = _CountingProvider(fail=False)
    registry = (RegisteredProvider("ollama", ProviderTier.LOCAL, provider, False),)
    payload = {"description": _UNPARSEABLE}
    try:
        c = _assisted_client(monkeypatch, store, registry)
        first = c.post("/adviser/recommend/assisted", json=payload)
        second = c.post("/adviser/recommend/assisted", json=payload)
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert first.status_code == second.status_code == 200
    assert provider.calls == 1  # the identical second request was collapsed


# --------------------------------------------------------------------------- #
# Proof-of-work: signed, single-use, expiring, IP-bound, constant-time.       #
# --------------------------------------------------------------------------- #


def _issue(store, config, ip_hash):
    return issue_challenge(store, config, ip_hash, FIXED_NOW)


def test_pow_happy_path_verifies_and_consumes_once() -> None:
    store = InMemoryAbuseStore()
    config = make_config(pow_difficulty=1)
    ip_hash = hashing.hash_ip(config.secret, _RAW_IP)
    issued = _issue(store, config, ip_hash)
    nonce = solve(issued.token, issued.difficulty)
    verification = verify_solution(config, issued.token, nonce, FIXED_NOW)
    assert verification.ok is True
    assert store.pow_consume(verification.challenge_id, ip_hash, FIXED_NOW) is True
    # Single-use: a replay of the same challenge is refused.
    assert store.pow_consume(verification.challenge_id, ip_hash, FIXED_NOW) is False


def test_pow_forged_signature_is_rejected() -> None:
    config = make_config()
    store = InMemoryAbuseStore()
    issued = _issue(store, config, hashing.hash_ip(config.secret, _RAW_IP))
    cid, iss, exp, diff, _sig = issued.token.split(".")
    forged = ".".join([cid, iss, exp, diff, "deadbeef"])
    assert verify_solution(config, forged, "0", FIXED_NOW).reason == "bad_signature"


def test_pow_downgraded_difficulty_breaks_signature() -> None:
    config = make_config(pow_difficulty=3)
    store = InMemoryAbuseStore()
    issued = _issue(store, config, hashing.hash_ip(config.secret, _RAW_IP))
    cid, iss, exp, _diff, sig = issued.token.split(".")
    downgraded = ".".join([cid, iss, exp, "0", sig])
    assert verify_solution(config, downgraded, "0", FIXED_NOW).reason == "bad_signature"


def test_pow_expired_token_is_rejected() -> None:
    config = make_config(pow_ttl_seconds=300)
    store = InMemoryAbuseStore()
    issued = _issue(store, config, hashing.hash_ip(config.secret, _RAW_IP))
    later = FIXED_NOW + timedelta(seconds=301)
    assert verify_solution(config, issued.token, "0", later).reason == "expired"


def test_pow_insufficient_work_is_rejected() -> None:
    config = make_config(pow_difficulty=4)
    store = InMemoryAbuseStore()
    issued = _issue(store, config, hashing.hash_ip(config.secret, _RAW_IP))
    # A nonce that does not meet 4 leading hex zeros (verified independently).
    bad_nonce = "1"
    digest = hashlib.sha256(f"{issued.token}:{bad_nonce}".encode()).hexdigest()
    assert not digest.startswith("0000")
    assert verify_solution(config, issued.token, bad_nonce, FIXED_NOW).reason == "insufficient_work"


@pytest.mark.parametrize("bad_token", ["only.three.parts", "a.b.c.d.e.f", "notanumber.x.y.z.s"])
def test_pow_malformed_token_is_rejected(bad_token: str) -> None:
    assert verify_solution(make_config(), bad_token, "0", FIXED_NOW).reason == "malformed_token"


def test_pow_is_ip_bound_on_consume() -> None:
    config = make_config()
    store = InMemoryAbuseStore()
    ip_hash = hashing.hash_ip(config.secret, _RAW_IP)
    issued = _issue(store, config, ip_hash)
    other_ip = hashing.hash_ip(config.secret, "198.51.100.9")
    assert store.pow_consume(issued.challenge_id, other_ip, FIXED_NOW) is False
    # The rightful IP can still consume it (it was never spent by the wrong IP).
    assert store.pow_consume(issued.challenge_id, ip_hash, FIXED_NOW) is True


def test_pow_verify_uses_constant_time_comparison() -> None:
    source = inspect.getsource(hashing.verify)
    assert "compare_digest" in source
