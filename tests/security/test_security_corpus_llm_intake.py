"""S1 -- LLM-assisted intake: fail-closed, no-publication, no-leak corpus.

Consolidates and extends the F007 slice-1 guarantees (docs/ARCHITECTURE "LLM
routing" + docs/SECURITY_PRIVACY_ABUSE):

* the recommendation is always deterministic -- an LLM only *proposes* a
  candidate that must survive the strict ``RecommendationRequest`` schema before
  it is trusted; malformed / oversized / unknown-field / URL-smuggling candidates
  and provider timeouts / errors all degrade to the deterministic fallback;
* there is no LLM-to-publication path (the assisted + structured handlers never
  write to the session they are handed, and the read-only session never commits);
* per-request consent is ephemeral -- it is never persisted (no schema column
  anywhere stores it) and is echoed only for the current response;
* the free-text description / prompt is never logged;
* the deterministic core does not even import the LLM package.

Everything is offline: a deterministic ``FakeInterpreter`` (owner decision Q1),
a synthetic catalogue, and a spy session -- no network, no real provider.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest
from app.adviser.abuse import InMemoryAbuseStore
from app.adviser.llm.fake import FakeInterpreter
from app.adviser.llm.protocol import ProviderTier
from app.adviser.llm.routing import (
    REASON_INVALID_INTERPRETATION,
    RegisteredProvider,
    route,
)
from app.adviser.llm.runtime import DEFAULT_LIMITS
from app.adviser.schema import MAX_REQUIREMENTS
from app.db import get_session
from app.main import app
from app.models import Base
from fastapi.testclient import TestClient

from tests.unit.test_adviser_router import _pool

_AI_LIMITS = DEFAULT_LIMITS.model_copy(
    update={"ai_requests_per_ip_per_day": 3, "require_captcha": False}
)

_VALID_REQUIREMENT = {
    "category": "object-file-storage",
    "demands": [{"metric": "storage", "amount": "5", "unit": "GB"}],
}
_VALID_CANDIDATE = {"requirements": [_VALID_REQUIREMENT]}

_UNPARSEABLE = "please help me choose something nice for my little project"


class _SpySession:
    """A session double that records any mutating call (there must be none)."""

    def __init__(self) -> None:
        self.writes: list[str] = []

    def _record(self, name):
        def _call(*args, **kwargs):
            self.writes.append(name)

        return _call

    def __getattr__(self, name: str):
        if name in {"add", "add_all", "delete", "merge", "commit", "flush", "bulk_save_objects"}:
            return self._record(name)
        raise AttributeError(name)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.adviser.router.gather_candidates", lambda _session, **_kw: _pool())
    monkeypatch.setattr("app.adviser.router.get_limits", lambda: DEFAULT_LIMITS)
    monkeypatch.setattr("app.adviser.router.get_registry", lambda: ())
    monkeypatch.setattr("app.adviser.router.get_abuse_store", lambda: InMemoryAbuseStore())
    app.dependency_overrides[get_session] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


# --------------------------------------------------------------------------- #
# SSRF / URL rejection on the public assisted endpoint (fail closed -> 422).  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "description",
    [
        "deploy to https://evil.example.com please",
        "fetch http://169.254.169.254/latest/meta-data",
        "use www.attacker.test for storage",
        "connect to db://internal-host/secret",
    ],
)
def test_url_in_description_is_rejected_422(client, description: str) -> None:
    r = client.post("/adviser/recommend/assisted", json={"description": description})
    assert r.status_code == 422


def test_unknown_field_and_empty_description_rejected(client) -> None:
    assert (
        client.post(
            "/adviser/recommend/assisted",
            json={"description": "object storage with 5 GB storage", "candidate": True},
        ).status_code
        == 422
    )
    assert client.post("/adviser/recommend/assisted", json={"description": ""}).status_code == 422


# --------------------------------------------------------------------------- #
# Strict schema rejects bad LLM *output* -> deterministic fallback.           #
# --------------------------------------------------------------------------- #


def _local_provider(candidate) -> tuple[RegisteredProvider, ...]:
    return (
        RegisteredProvider(
            "ollama", ProviderTier.LOCAL, FakeInterpreter(candidate=candidate), False
        ),
    )


@pytest.mark.parametrize(
    "bad_candidate",
    [
        {"requirements": []},  # empty (min_length=1)
        {"requirements": [{"category": "object-file-storage"}]},  # missing demands
        {
            "requirements": [
                {
                    "category": "not-a-real-category",
                    "demands": [{"metric": "storage", "amount": "1", "unit": "GB"}],
                }
            ]
        },  # bad category
        {"requirements": [_VALID_REQUIREMENT], "bogus_field": 1},  # extra=forbid
        {"requirements": [dict(_VALID_REQUIREMENT, label="http://evil")]},  # URL smuggled
        {"requirements": [_VALID_REQUIREMENT] * (MAX_REQUIREMENTS + 1)},  # oversized
        "not even a dict",  # wrong type entirely
    ],
)
def test_malformed_llm_output_falls_back_deterministically(bad_candidate) -> None:
    outcome = route(_UNPARSEABLE, _AI_LIMITS, _local_provider(bad_candidate))
    assert outcome.llm_used is False
    assert outcome.interpretation is None
    assert outcome.fallback_reason == REASON_INVALID_INTERPRETATION


@pytest.mark.parametrize(
    "kwargs,reason",
    [({"raise_timeout": True}, "provider_timeout"), ({"raise_error": True}, "provider_error")],
)
def test_provider_failure_degrades_to_fallback(kwargs: dict, reason: str) -> None:
    provider = (RegisteredProvider("ollama", ProviderTier.LOCAL, FakeInterpreter(**kwargs), False),)
    outcome = route(_UNPARSEABLE, _AI_LIMITS, provider)
    assert outcome.llm_used is False
    assert outcome.interpretation is None
    assert outcome.fallback_reason == reason


def test_valid_llm_output_is_used_but_still_deterministic() -> None:
    outcome = route(_UNPARSEABLE, _AI_LIMITS, _local_provider(_VALID_CANDIDATE))
    assert outcome.llm_used is True
    assert outcome.interpretation is not None  # the trusted, schema-valid request


# --------------------------------------------------------------------------- #
# Deterministic fallback is byte-identical with providers disabled.           #
# --------------------------------------------------------------------------- #


def test_assisted_matches_structured_with_providers_disabled(client) -> None:
    assisted = client.post(
        "/adviser/recommend/assisted",
        json={"description": "object storage with 5 GB storage"},
    )
    assert assisted.status_code == 200
    body = assisted.json()
    assert body["routing"]["llm_used"] is False
    structured = client.post("/adviser/recommend", json=_VALID_CANDIDATE)
    assert structured.status_code == 200
    assert body["recommendation"] == structured.json()


# --------------------------------------------------------------------------- #
# No LLM-to-publication path: neither handler writes to the session.          #
# --------------------------------------------------------------------------- #


def test_assisted_llm_path_writes_nothing_to_the_session(monkeypatch) -> None:
    spy = _SpySession()
    fake = RegisteredProvider(
        "ollama", ProviderTier.LOCAL, FakeInterpreter(candidate=_VALID_CANDIDATE), False
    )
    monkeypatch.setattr("app.adviser.router.gather_candidates", lambda _s, **_kw: _pool())
    monkeypatch.setattr("app.adviser.router.get_limits", lambda: _AI_LIMITS)
    monkeypatch.setattr("app.adviser.router.get_registry", lambda: (fake,))
    monkeypatch.setattr("app.adviser.router.get_abuse_store", lambda: InMemoryAbuseStore())
    app.dependency_overrides[get_session] = lambda: spy
    try:
        c = TestClient(app)
        r = c.post(
            "/adviser/recommend/assisted",
            json={"description": _UNPARSEABLE, "consent": {"external_processing": True}},
        )
        assert r.status_code == 200
        assert r.json()["routing"]["llm_used"] is True
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert spy.writes == []


def test_structured_endpoint_writes_nothing_to_the_session(client) -> None:
    spy = _SpySession()
    app.dependency_overrides[get_session] = lambda: spy
    try:
        c = TestClient(app)
        assert c.post("/adviser/recommend", json=_VALID_CANDIDATE).status_code == 200
    finally:
        app.dependency_overrides[get_session] = lambda: None
    assert spy.writes == []


def test_read_only_session_dependency_never_commits() -> None:
    import inspect

    from app import db

    source = inspect.getsource(db.get_session)
    assert ".commit(" not in source
    assert "rollback" in source


# --------------------------------------------------------------------------- #
# Consent is ephemeral: no schema column anywhere persists it.                #
# --------------------------------------------------------------------------- #


def test_no_orm_column_persists_consent_or_description() -> None:
    # Per-request consent and the assisted free-text prompt are ephemeral: no
    # persisted column anywhere stores them. (The catalogue's static reference
    # ``description`` columns are unrelated and deliberately not matched.)
    forbidden = ("consent", "prompt", "free_text", "freetext", "user_description")
    for table in Base.metadata.tables.values():
        for column in table.columns:
            name = column.name.lower()
            for token in forbidden:
                assert token not in name, (
                    f"{table.name}.{column.name} looks like it persists ephemeral input"
                )


# --------------------------------------------------------------------------- #
# The description / prompt is never logged.                                   #
# --------------------------------------------------------------------------- #


def test_description_is_never_logged(client, caplog) -> None:
    canary = "CANARY-b7f2e1a9-do-not-log-this-object-storage-5-gb"
    with caplog.at_level(logging.DEBUG):
        r = client.post("/adviser/recommend/assisted", json={"description": canary})
    assert r.status_code == 200
    assert canary not in caplog.text
    for record in caplog.records:
        assert canary not in record.getMessage()
        assert canary not in str(getattr(record, "args", ""))


# --------------------------------------------------------------------------- #
# The deterministic core must not import the LLM package (fresh subprocess).  #
# --------------------------------------------------------------------------- #


def test_deterministic_core_does_not_import_llm_package() -> None:
    app_dir = Path(__file__).resolve().parents[2] / "apps" / "api"
    probe = (
        "import sys\n"
        "import app.adviser.recommend\n"
        "import app.adviser.select\n"
        "import app.adviser.schema\n"
        "import app.adviser.schemas\n"
        "leaked = [m for m in sys.modules if m.startswith('app.adviser.llm')]\n"
        "sys.exit(1 if leaked else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=str(app_dir), capture_output=True, text=True
    )
    assert result.returncode == 0, f"core imported the LLM package: {result.stderr.strip()}"
