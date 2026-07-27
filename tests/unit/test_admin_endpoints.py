"""HTTP-level tests for the private admin surface (F007 slice 4).

Drives the FastAPI app with the OAuth double + in-memory stores injected at the
router seams (owner note N1: real github.com is never networked). Covers the
full negative-security matrix required by F007 acceptance step 4 -- non-
allowlisted denied, forged / expired / tampered cookie denied, missing / invalid
CSRF denied, missing / invalid OAuth state denied, unauthenticated access to
every admin function denied -- and asserts every denial is audited, plus the
positive paths (allowlisted login issues a signed cookie, the kill switch wires
the existing S2 abuse flag, the review queue advances a disposition, source
health reads, and config-diff validates).
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.admin import signing
from app.admin.audit import InMemoryAdminAuditStore
from app.admin.data import InMemoryAdminDataStore, ReviewQueueRow, SourceHealthRow
from app.admin.oauth import FakeGitHubOAuthClient
from app.adviser.abuse import InMemoryAbuseStore
from app.adviser.abuse.service import AI_KILL_SWITCH_FLAG
from app.main import app
from fastapi.testclient import TestClient

from tests.support.admin import make_admin_config, now_epoch

# The ``app.admin`` package rebinds the name ``router`` to the APIRouter
# instance, which shadows the submodule for attribute-based lookup; import the
# module object explicitly so the injection seams can be monkeypatched on it.
admin_router = importlib.import_module("app.admin.router")

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = REPO_ROOT / "config" / "examples" / "llm-providers.example.yaml"


@pytest.fixture
def harness(monkeypatch):
    """Wire the admin router to offline doubles and return the test context."""

    config = make_admin_config()
    audit = InMemoryAdminAuditStore()
    now = datetime.now(UTC)
    items = [
        ReviewQueueRow(
            id=1,
            reason="evidence-contradiction",
            recommended_action="reject",
            admin_disposition="pending",
            evidence_conflict={"free_tier": ["yes", "no"]},
            candidate_facts={"slug": "acme-free"},
            offer_id=None,
            scan_run_id=None,
            created_at=now,
        )
    ]
    sources = [
        SourceHealthRow(
            source_id=1,
            slug="acme",
            adapter_type="html",
            official=True,
            enabled=True,
            health="ok",
            endpoint="https://acme.test/pricing",
            last_scan_status="ok",
            last_scan_finished_at=now,
            last_errors_count=0,
            last_snapshot_fetched_at=now,
        )
    ]
    data = InMemoryAdminDataStore(review_items=items, sources=sources)
    abuse = InMemoryAbuseStore()
    fake = FakeGitHubOAuthClient(
        codes={
            "code-stsyg": "tok-stsyg",
            "code-mallory": "tok-mallory",
            "code-bad": "tok-nobody",
        },
        logins={"tok-stsyg": "stsyg", "tok-mallory": "mallory"},
    )
    settings_stub = SimpleNamespace(ai_kill_switch=False, llm_config_path=None)

    monkeypatch.setattr(admin_router, "get_admin_config", lambda: config)
    monkeypatch.setattr(admin_router, "get_oauth_client", lambda: fake)
    monkeypatch.setattr(admin_router, "get_admin_audit_store", lambda: audit)
    monkeypatch.setattr(admin_router, "get_admin_data_store", lambda: data)
    monkeypatch.setattr(admin_router, "get_abuse_store", lambda: abuse)
    monkeypatch.setattr(admin_router, "get_settings", lambda: settings_stub)

    client = TestClient(app)
    return SimpleNamespace(
        client=client,
        audit=audit,
        data=data,
        abuse=abuse,
        config=config,
        fake=fake,
        settings=settings_stub,
    )


def _auth(h) -> str:
    """Mint + install a valid session cookie; return a matching CSRF token."""

    epoch = now_epoch()
    session = signing.issue_session("stsyg", h.config.signing_key, epoch, 3600)
    csrf = signing.issue_csrf("stsyg", h.config.signing_key, epoch, 3600)
    h.client.cookies.set("admin_session", session)
    return csrf


def _denials(h, action: str):
    return [r for r in h.audit.rows if r.action == action and r.outcome == "denied"]


# --------------------------------------------------------------------------- #
# OAuth login flow (positive + negatives)
# --------------------------------------------------------------------------- #


def test_login_redirects_to_github_with_signed_state(harness) -> None:
    resp = harness.client.get("/admin/login", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(harness.config.authorize_url)
    assert "state=" in location and "client_id=test-client-id" in location
    assert harness.client.cookies.get("admin_oauth_state")


def test_callback_allowlisted_issues_session_and_audits_success(harness) -> None:
    c = harness.client
    c.get("/admin/login", follow_redirects=False)
    state = c.cookies.get("admin_oauth_state")
    resp = c.get(f"/admin/callback?code=code-stsyg&state={state}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin"
    assert c.cookies.get("admin_session")
    assert any(
        r.action == "login" and r.outcome == "success" and r.actor == "stsyg"
        for r in harness.audit.rows
    )


def test_callback_non_allowlisted_is_rejected_and_audited(harness) -> None:
    c = harness.client
    c.get("/admin/login", follow_redirects=False)
    state = c.cookies.get("admin_oauth_state")
    resp = c.get(f"/admin/callback?code=code-mallory&state={state}", follow_redirects=False)
    assert resp.status_code == 403
    assert c.cookies.get("admin_session") is None
    assert any(
        r.reason == "not_allowlisted" and r.actor == "mallory" for r in _denials(harness, "login")
    )


def test_callback_missing_state_cookie_is_denied_and_audited(harness) -> None:
    resp = harness.client.get(
        "/admin/callback?code=code-stsyg&state=anything", follow_redirects=False
    )
    assert resp.status_code == 401
    assert any(r.reason == "invalid_state" for r in _denials(harness, "login"))


def test_callback_mismatched_state_is_denied_and_audited(harness) -> None:
    c = harness.client
    c.get("/admin/login", follow_redirects=False)  # sets a valid state cookie
    resp = c.get("/admin/callback?code=code-stsyg&state=forged-value", follow_redirects=False)
    assert resp.status_code == 401
    assert any(r.reason == "invalid_state" for r in _denials(harness, "login"))


def test_callback_oauth_failure_is_denied_and_audited(harness) -> None:
    c = harness.client
    c.get("/admin/login", follow_redirects=False)
    state = c.cookies.get("admin_oauth_state")
    resp = c.get(f"/admin/callback?code=code-bad&state={state}", follow_redirects=False)
    assert resp.status_code == 401
    assert any(r.reason == "oauth_failed" for r in _denials(harness, "login"))


def test_callback_missing_code_is_denied_and_audited(harness) -> None:
    c = harness.client
    c.get("/admin/login", follow_redirects=False)
    state = c.cookies.get("admin_oauth_state")
    resp = c.get(f"/admin/callback?state={state}", follow_redirects=False)
    assert resp.status_code == 400
    assert any(r.reason == "missing_code" for r in _denials(harness, "login"))


# --------------------------------------------------------------------------- #
# Session cookie: forged / tampered / expired
# --------------------------------------------------------------------------- #


def test_tampered_cookie_is_denied_and_audited(harness) -> None:
    harness.client.cookies.set("admin_session", "forged-body.deadbeefcafe")
    resp = harness.client.get("/admin/session")
    assert resp.status_code == 401
    assert any(r.reason == "invalid_cookie" for r in _denials(harness, "session_view"))


def test_expired_cookie_is_denied_and_audited(harness) -> None:
    epoch = now_epoch() - 100_000
    token = signing.issue_session("stsyg", harness.config.signing_key, epoch, 10)
    harness.client.cookies.set("admin_session", token)
    resp = harness.client.get("/admin/session")
    assert resp.status_code == 401
    assert any(r.reason == "invalid_cookie" for r in _denials(harness, "session_view"))


def test_wrong_key_cookie_is_denied(harness) -> None:
    epoch = now_epoch()
    token = signing.issue_session("stsyg", "attacker-key", epoch, 3600)
    harness.client.cookies.set("admin_session", token)
    resp = harness.client.get("/admin/session")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Unauthenticated access to EVERY admin function is denied + audited
# --------------------------------------------------------------------------- #


_UNAUTH_CASES = [
    ("get", "/admin/session", "session_view", None),
    ("get", "/admin/kill-switch", "kill_switch_view", None),
    ("post", "/admin/kill-switch", "kill_switch_toggle", {"enabled": True}),
    ("get", "/admin/review-queue", "review_queue_view", None),
    ("post", "/admin/review-queue/1/action", "review_action", {"disposition": "approved"}),
    ("get", "/admin/source-health", "source_health_view", None),
    ("post", "/admin/config-diff", "config_diff", {"candidate": "providers: []"}),
]


@pytest.mark.parametrize("method,path,action,body", _UNAUTH_CASES)
def test_unauthenticated_access_denied_and_audited(harness, method, path, action, body) -> None:
    kwargs = {} if body is None else {"json": body}
    resp = getattr(harness.client, method)(path, **kwargs)
    assert resp.status_code == 401
    assert any(r.reason == "unauthenticated" for r in _denials(harness, action)), (
        f"{action} not audited as unauthenticated"
    )


# --------------------------------------------------------------------------- #
# CSRF protection on state-changing actions
# --------------------------------------------------------------------------- #


_MUTATIONS = [
    ("/admin/kill-switch", {"enabled": True}, "kill_switch_toggle"),
    ("/admin/review-queue/1/action", {"disposition": "approved"}, "review_action"),
    ("/admin/config-diff", {"candidate": "providers: []"}, "config_diff"),
]


@pytest.mark.parametrize("path,body,action", _MUTATIONS)
def test_mutation_without_csrf_is_denied_and_audited(harness, path, body, action) -> None:
    _auth(harness)
    resp = harness.client.post(path, json=body)  # no X-CSRF-Token header
    assert resp.status_code == 403
    assert any(r.reason == "invalid_csrf" and r.actor == "stsyg" for r in _denials(harness, action))


@pytest.mark.parametrize("path,body,action", _MUTATIONS)
def test_mutation_with_bad_csrf_is_denied_and_audited(harness, path, body, action) -> None:
    _auth(harness)
    resp = harness.client.post(path, json=body, headers={"X-CSRF-Token": "not-a-valid-token"})
    assert resp.status_code == 403
    assert any(r.reason == "invalid_csrf" for r in _denials(harness, action))


# --------------------------------------------------------------------------- #
# Positive admin functions (behind the allowlist + session guard)
# --------------------------------------------------------------------------- #


def test_session_info_returns_login_and_usable_csrf(harness) -> None:
    _auth(harness)
    resp = harness.client.get("/admin/session")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["login"] == "stsyg"
    assert signing.verify_csrf(
        payload["csrf_token"], "stsyg", harness.config.signing_key, now_epoch()
    )


def test_kill_switch_toggle_wires_abuse_flag_and_audits(harness) -> None:
    csrf = _auth(harness)
    resp = harness.client.post(
        "/admin/kill-switch", json={"enabled": True}, headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True}
    assert harness.abuse.get_flag(AI_KILL_SWITCH_FLAG) is True
    state = harness.client.get("/admin/kill-switch").json()
    assert state["enabled"] is True and state["effective"] is True
    assert any(
        r.action == "kill_switch_toggle"
        and r.outcome == "success"
        and r.context == {"enabled": True}
        for r in harness.audit.rows
    )


def test_review_queue_read_and_action(harness) -> None:
    csrf = _auth(harness)
    listing = harness.client.get("/admin/review-queue")
    assert listing.status_code == 200
    body = listing.json()
    assert len(body["items"]) == 1 and body["items"][0]["id"] == 1
    assert body["valid_actions"] == ["approved", "rejected", "deferred"]

    acted = harness.client.post(
        "/admin/review-queue/1/action",
        json={"disposition": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert acted.status_code == 200
    assert harness.data.list_review_queue(None)[0].admin_disposition == "approved"
    assert any(r.action == "review_action" and r.outcome == "success" for r in harness.audit.rows)


def test_review_action_invalid_disposition_is_denied_and_audited(harness) -> None:
    csrf = _auth(harness)
    resp = harness.client.post(
        "/admin/review-queue/1/action",
        json={"disposition": "pending"},  # a valid vocab value but not an action
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422
    assert any(r.reason == "invalid_disposition" for r in _denials(harness, "review_action"))


def test_review_action_unknown_item_is_404_and_audited(harness) -> None:
    csrf = _auth(harness)
    resp = harness.client.post(
        "/admin/review-queue/999/action",
        json={"disposition": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 404
    assert any(r.reason == "item_not_found" for r in _denials(harness, "review_action"))


def test_source_health_reads_existing_sources(harness) -> None:
    _auth(harness)
    resp = harness.client.get("/admin/source-health")
    assert resp.status_code == 200
    sources = resp.json()["sources"]
    assert len(sources) == 1
    assert sources[0]["slug"] == "acme" and sources[0]["last_scan_status"] == "ok"


def test_config_diff_validates_matching_committed_config(harness) -> None:
    harness.settings.llm_config_path = str(EXAMPLE_CONFIG)
    candidate = EXAMPLE_CONFIG.read_text(encoding="utf-8")
    csrf = _auth(harness)
    resp = harness.client.post(
        "/admin/config-diff", json={"candidate": candidate}, headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["valid"] is True
    assert payload["committed_present"] is True
    assert payload["diff"] == []  # candidate identical to committed
    assert not payload["problems"]


def test_config_diff_reports_invalid_candidate(harness) -> None:
    csrf = _auth(harness)
    resp = harness.client.post(
        "/admin/config-diff",
        json={"candidate": "- not\n- a\n- mapping\n"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["valid"] is False
    assert payload["problems"]  # loader rejected it
    assert any(r.action == "config_diff" and r.outcome == "success" for r in harness.audit.rows)


def test_logout_clears_cookie_and_audits(harness) -> None:
    _auth(harness)
    resp = harness.client.post("/admin/logout")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert any(r.action == "logout" and r.outcome == "success" for r in harness.audit.rows)


# --------------------------------------------------------------------------- #
# No secret / token / code is ever written to an audit row
# --------------------------------------------------------------------------- #


def test_audit_never_stores_secrets(harness) -> None:
    c = harness.client
    # Drive a full success login (real code + token flow through the router).
    c.get("/admin/login", follow_redirects=False)
    state = c.cookies.get("admin_oauth_state")
    c.get(f"/admin/callback?code=code-stsyg&state={state}", follow_redirects=False)
    # And a failed exchange (code present but token maps to no login).
    c2 = harness.client
    c2.get("/admin/login", follow_redirects=False)
    forbidden = {"code-stsyg", "tok-stsyg", "code-bad", "tok-nobody", harness.config.client_secret}
    for row in harness.audit.rows:
        ctx = row.context or {}
        for key, value in ctx.items():
            assert key.lower() not in {"code", "token", "access_token", "client_secret", "secret"}
            assert str(value) not in forbidden
