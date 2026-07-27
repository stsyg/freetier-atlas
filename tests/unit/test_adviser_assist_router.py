"""Unit tests for POST /adviser/recommend/assisted (F007 slice 1).

Uses a synthetic catalogue pool (no DB) and, by default, an empty provider
registry so the endpoint exercises the deterministic path. Proves the assisted
result is byte-identical to POST /adviser/recommend for the equivalent
structured requirements, that non-interpretable input degrades gracefully, that
GET is not allowed, that URLs and over-limit input are rejected, and that an
enabled fake provider surfaces llm_used/consent correctly.
"""

from __future__ import annotations

import pytest
from app.adviser.abuse import InMemoryAbuseStore
from app.adviser.llm.fake import FakeInterpreter
from app.adviser.llm.protocol import ProviderTier
from app.adviser.llm.routing import RegisteredProvider
from app.adviser.llm.runtime import DEFAULT_LIMITS
from app.db import get_session
from app.main import app
from fastapi.testclient import TestClient

from tests.unit.test_adviser_router import _pool

#: Limits granting a small AI budget (and no proof-of-work) so the injected-fake
#: provider tests can exercise the LLM path under the S2 abuse enforcement. The
#: default limits ship ``ai_requests_per_ip_per_day=0``, which now correctly
#: forces the deterministic path.
_AI_LIMITS = DEFAULT_LIMITS.model_copy(
    update={"ai_requests_per_ip_per_day": 3, "require_captcha": False}
)

_CANDIDATE = {
    "requirements": [
        {
            "category": "object-file-storage",
            "demands": [{"metric": "storage", "amount": "5", "unit": "GB"}],
        }
    ]
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.adviser.router.gather_candidates", lambda _session: _pool())
    monkeypatch.setattr("app.adviser.router.get_limits", lambda: DEFAULT_LIMITS)
    monkeypatch.setattr("app.adviser.router.get_registry", lambda: ())
    store = InMemoryAbuseStore()
    monkeypatch.setattr("app.adviser.router.get_abuse_store", lambda: store)
    app.dependency_overrides[get_session] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_parseable_description_matches_structured_recommendation(client) -> None:
    assisted = client.post(
        "/adviser/recommend/assisted",
        json={"description": "object storage with 5 GB storage"},
    )
    assert assisted.status_code == 200
    body = assisted.json()
    assert body["interpreted"] is True
    assert body["routing"]["llm_used"] is False
    assert body["routing"]["fallback_reason"] == "deterministic_parser"

    structured = client.post("/adviser/recommend", json=_CANDIDATE)
    assert structured.status_code == 200
    # The deterministic recommendation embedded in the assisted response is
    # byte-identical to the structured endpoint's output.
    assert body["recommendation"] == structured.json()


def test_non_interpretable_description_degrades_gracefully(client) -> None:
    r = client.post(
        "/adviser/recommend/assisted",
        json={"description": "hi there, just wanted to chat"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["interpreted"] is False
    assert body["recommendation"] is None
    assert body["routing"]["llm_used"] is False
    assert body["routing"]["fallback_reason"] == "no_provider_enabled"


def test_consent_absent_still_returns_deterministic_result(client) -> None:
    r = client.post(
        "/adviser/recommend/assisted",
        json={
            "description": "object storage with 5 GB storage",
            "consent": {"external_processing": False},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["consent"]["external_processing_requested"] is False
    assert body["consent"]["external_processing_used"] is False
    assert body["interpreted"] is True


def test_get_is_not_allowed(client) -> None:
    assert client.get("/adviser/recommend/assisted").status_code == 405


def test_url_in_description_is_rejected(client) -> None:
    r = client.post(
        "/adviser/recommend/assisted",
        json={"description": "deploy to https://evil.example.com please"},
    )
    assert r.status_code == 422


def test_over_limit_description_is_rejected(client) -> None:
    big = "object storage " * 500  # far exceeds the 2000-char default limit
    r = client.post("/adviser/recommend/assisted", json={"description": big})
    assert r.status_code == 422


def test_unknown_field_is_rejected(client) -> None:
    r = client.post(
        "/adviser/recommend/assisted",
        json={"description": "object storage with 5 GB storage", "candidate": True},
    )
    assert r.status_code == 422


def test_empty_description_is_rejected(client) -> None:
    assert client.post("/adviser/recommend/assisted", json={"description": ""}).status_code == 422


def test_enabled_fake_provider_surfaces_llm_used(monkeypatch) -> None:
    # With the deterministic parser unable to interpret, an enabled local fake
    # provider produces the interpretation -> llm_used true, deterministic result.
    fake = RegisteredProvider(
        "ollama", ProviderTier.LOCAL, FakeInterpreter(candidate=_CANDIDATE), False
    )
    monkeypatch.setattr("app.adviser.router.gather_candidates", lambda _s: _pool())
    monkeypatch.setattr("app.adviser.router.get_limits", lambda: _AI_LIMITS)
    monkeypatch.setattr("app.adviser.router.get_registry", lambda: (fake,))
    monkeypatch.setattr("app.adviser.router.get_abuse_store", lambda: InMemoryAbuseStore())
    app.dependency_overrides[get_session] = lambda: None
    try:
        c = TestClient(app)
        r = c.post(
            "/adviser/recommend/assisted",
            json={"description": "help me pick something for my app"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["routing"]["llm_used"] is True
        assert body["routing"]["llm_provider"] == "ollama"
        assert body["interpreted"] is True
        assert body["recommendation"] is not None
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_consent_gated_external_used_with_consent(monkeypatch) -> None:
    fake = RegisteredProvider(
        "gemini", ProviderTier.FREE_HOSTED, FakeInterpreter(candidate=_CANDIDATE), True
    )
    monkeypatch.setattr("app.adviser.router.gather_candidates", lambda _s: _pool())
    monkeypatch.setattr("app.adviser.router.get_limits", lambda: _AI_LIMITS)
    monkeypatch.setattr("app.adviser.router.get_registry", lambda: (fake,))
    monkeypatch.setattr("app.adviser.router.get_abuse_store", lambda: InMemoryAbuseStore())
    app.dependency_overrides[get_session] = lambda: None
    try:
        c = TestClient(app)
        r = c.post(
            "/adviser/recommend/assisted",
            json={
                "description": "help me pick something for my app",
                "consent": {"external_processing": True},
            },
        )
        body = r.json()
        assert body["routing"]["llm_used"] is True
        assert body["consent"]["external_processing_used"] is True
    finally:
        app.dependency_overrides.pop(get_session, None)
