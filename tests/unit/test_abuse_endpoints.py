"""HTTP-level tests for the S2 abuse controls on the adviser endpoints."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from app.adviser.abuse import InMemoryAbuseStore
from app.adviser.abuse.service import AI_KILL_SWITCH_FLAG
from app.adviser.llm.fake import FakeInterpreter
from app.adviser.llm.protocol import ProviderTier
from app.adviser.llm.routing import RegisteredProvider
from app.adviser.llm.runtime import DEFAULT_LIMITS
from app.db import get_session
from app.main import app
from fastapi.testclient import TestClient

from tests.support.abuse import make_config
from tests.unit.test_adviser_router import _pool

_CANDIDATE = {
    "requirements": [
        {
            "category": "object-file-storage",
            "demands": [{"metric": "storage", "amount": "5", "unit": "GB"}],
        }
    ]
}

_AI_LIMITS = DEFAULT_LIMITS.model_copy(
    update={"ai_requests_per_ip_per_day": 5, "require_captcha": False}
)
_POW_LIMITS = DEFAULT_LIMITS.model_copy(
    update={"ai_requests_per_ip_per_day": 0, "require_captcha": True}
)
_QUOTA_LIMITS = DEFAULT_LIMITS.model_copy(
    update={"ai_requests_per_ip_per_day": 0, "require_captcha": False}
)


def _det_body(name: str) -> dict:
    return {
        "workload_name": name,
        "requirements": [
            {
                "category": "object-file-storage",
                "demands": [{"metric": "storage", "amount": "5", "unit": "GB", "period": "month"}],
            }
        ],
    }


def _make_client(monkeypatch, *, limits, registry, store):
    monkeypatch.setattr("app.adviser.router.gather_candidates", lambda _s, **_kw: _pool())
    monkeypatch.setattr("app.adviser.router.get_limits", lambda: limits)
    monkeypatch.setattr("app.adviser.router.get_registry", lambda: registry)
    monkeypatch.setattr("app.adviser.router.get_abuse_store", lambda: store)
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup():
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_session, None)


# --- deterministic per-IP rate limiting ------------------------------------


def test_deterministic_overage_returns_429_with_retry_after(monkeypatch) -> None:
    limits = DEFAULT_LIMITS.model_copy(update={"deterministic_requests_per_ip_per_day": 2})
    client = _make_client(monkeypatch, limits=limits, registry=(), store=InMemoryAbuseStore())
    assert client.post("/adviser/recommend", json=_det_body("a")).status_code == 200
    assert client.post("/adviser/recommend", json=_det_body("b")).status_code == 200
    over = client.post("/adviser/recommend", json=_det_body("c"))
    assert over.status_code == 429
    assert int(over.headers["Retry-After"]) > 0


def test_deterministic_duplicate_does_not_burn_budget(monkeypatch) -> None:
    limits = DEFAULT_LIMITS.model_copy(update={"deterministic_requests_per_ip_per_day": 1})
    client = _make_client(monkeypatch, limits=limits, registry=(), store=InMemoryAbuseStore())
    assert client.post("/adviser/recommend", json=_det_body("a")).status_code == 200
    # Identical body inside the dedupe window is collapsed -> still 200, no 429.
    assert client.post("/adviser/recommend", json=_det_body("a")).status_code == 200


def test_export_shares_the_deterministic_limit(monkeypatch) -> None:
    limits = DEFAULT_LIMITS.model_copy(update={"deterministic_requests_per_ip_per_day": 1})
    client = _make_client(monkeypatch, limits=limits, registry=(), store=InMemoryAbuseStore())
    assert client.post("/adviser/export", json=_det_body("a")).status_code == 200
    over = client.post("/adviser/export", json=_det_body("b"))
    assert over.status_code == 429


# --- proof-of-work challenge endpoint --------------------------------------


def test_challenge_endpoint_returns_solvable_token(monkeypatch) -> None:
    client = _make_client(
        monkeypatch, limits=DEFAULT_LIMITS, registry=(), store=InMemoryAbuseStore()
    )
    r = client.post("/adviser/challenge")
    assert r.status_code == 200
    body = r.json()
    token, difficulty = body["token"], body["difficulty"]
    assert body["algorithm"]
    # The advertised challenge is genuinely solvable.
    nonce = 0
    while not hashlib.sha256(f"{token}:{nonce}".encode()).hexdigest().startswith("0" * difficulty):
        nonce += 1
    assert nonce >= 0


# --- assisted graceful degradation -----------------------------------------


def _fake_registry(fake: FakeInterpreter, *, tier=ProviderTier.LOCAL, consent=False):
    return (RegisteredProvider("ollama", tier, fake, consent),)


def test_assisted_degrades_under_kill_switch(monkeypatch) -> None:
    store = InMemoryAbuseStore()
    store.set_flag(AI_KILL_SWITCH_FLAG, True, datetime.now(UTC))
    fake = FakeInterpreter(candidate=_CANDIDATE)
    client = _make_client(
        monkeypatch, limits=_AI_LIMITS, registry=_fake_registry(fake), store=store
    )
    r = client.post("/adviser/recommend/assisted", json={"description": "help me pick something"})
    assert r.status_code == 200
    body = r.json()
    assert body["routing"]["llm_used"] is False
    assert body["routing"]["fallback_reason"] == "ai_kill_switch"
    assert fake.calls == 0  # the LLM path was never entered


def test_assisted_degrades_under_quota_exhaustion(monkeypatch) -> None:
    fake = FakeInterpreter(candidate=_CANDIDATE)
    client = _make_client(
        monkeypatch,
        limits=_QUOTA_LIMITS,
        registry=_fake_registry(fake),
        store=InMemoryAbuseStore(),
    )
    r = client.post("/adviser/recommend/assisted", json={"description": "help me pick something"})
    assert r.status_code == 200
    body = r.json()
    assert body["routing"]["llm_used"] is False
    assert body["routing"]["fallback_reason"] == "ai_quota_exhausted"


def test_assisted_requires_pow_then_accepts_a_valid_solution(monkeypatch) -> None:
    store = InMemoryAbuseStore()
    fake = FakeInterpreter(candidate=_CANDIDATE)
    client = _make_client(
        monkeypatch, limits=_POW_LIMITS, registry=_fake_registry(fake), store=store
    )

    # Beyond the (zero) free threshold with captcha required -> PoW demanded.
    first = client.post(
        "/adviser/recommend/assisted", json={"description": "help me choose a stack"}
    )
    assert first.status_code == 200
    assert first.json()["routing"]["fallback_reason"] == "pow_required"

    # Obtain, solve, and submit a challenge on a *distinct* request body.
    challenge = client.post("/adviser/challenge").json()
    token, difficulty = challenge["token"], challenge["difficulty"]
    nonce = 0
    while not hashlib.sha256(f"{token}:{nonce}".encode()).hexdigest().startswith("0" * difficulty):
        nonce += 1

    accepted = client.post(
        "/adviser/recommend/assisted",
        json={"description": "please help me choose a different stack"},
        headers={"X-PoW-Token": token, "X-PoW-Nonce": str(nonce)},
    )
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["routing"]["llm_used"] is True
    assert fake.calls == 1


def test_assisted_dedupe_collapses_duplicate_llm_calls(monkeypatch) -> None:
    store = InMemoryAbuseStore()
    fake = FakeInterpreter(candidate=_CANDIDATE)
    client = _make_client(
        monkeypatch, limits=_AI_LIMITS, registry=_fake_registry(fake), store=store
    )
    payload = {"description": "help me choose something for my project"}
    first = client.post("/adviser/recommend/assisted", json=payload)
    assert first.json()["routing"]["llm_used"] is True
    second = client.post("/adviser/recommend/assisted", json=payload)
    assert second.json()["routing"]["llm_used"] is False
    assert second.json()["routing"]["fallback_reason"] == "deduplicated"
    assert fake.calls == 1  # the duplicate did not reach the provider


def test_assisted_circuit_breaker_short_circuits_a_failing_provider(monkeypatch) -> None:
    store = InMemoryAbuseStore()
    monkeypatch.setattr(
        "app.adviser.router.load_abuse_config",
        lambda: make_config(breaker_threshold=2, breaker_cooldown_seconds=300),
    )
    fake = FakeInterpreter(raise_error=True)
    client = _make_client(
        monkeypatch, limits=_AI_LIMITS, registry=_fake_registry(fake), store=store
    )
    for i in range(4):
        r = client.post(
            "/adviser/recommend/assisted", json={"description": f"help me choose option {i}"}
        )
        assert r.status_code == 200
        assert r.json()["routing"]["llm_used"] is False
    # Breaker opened after 2 failures; the 3rd/4th calls never reached the provider.
    assert fake.calls == 2
