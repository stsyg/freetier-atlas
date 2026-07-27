"""S4 -- private admin surface: authn / CSRF / OAuth-state / audit no-leak corpus.

Consolidates and extends the F007 slice-4 negative-security matrix (docs
SECURITY_PRIVACY_ABUSE + owner decisions Q6 / N1). Drives the FastAPI app with an
offline OAuth double and in-memory stores injected at the router seams -- real
github.com is never networked.

Adversarial coverage:

* a non-allowlisted GitHub login is denied (403), issued **no** session cookie,
  and audited;
* unauthenticated access to **every** guarded admin function is denied (401) --
  each POST is sent with a *valid* body so the body validator never masks the
  auth check -- and audited;
* a forged / tampered / expired / wrong-key session cookie is denied (401) across
  **every** guarded endpoint;
* a missing / invalid CSRF token is denied (403) on **every** mutation;
* a missing / mismatched / forged OAuth state is denied (401), and a matching but
  signature-invalid state is still denied; a missing code is 400; a failed
  exchange is 401;
* every audit row (success and denied) is scanned to prove it carries **no**
  secret, token, cookie, signing key, client secret, or raw OAuth code.
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.admin import signing
from app.admin.audit import InMemoryAdminAuditStore
from app.admin.data import QUEUE_ACTIONS, InMemoryAdminDataStore, ReviewQueueRow, SourceHealthRow
from app.admin.oauth import FakeGitHubOAuthClient
from app.adviser.abuse import InMemoryAbuseStore
from app.main import app
from fastapi.testclient import TestClient

from tests.support.admin import make_admin_config, now_epoch

admin_router = importlib.import_module("app.admin.router")

_WRONG_KEY = "an-entirely-different-signing-key"


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
        codes={"code-stsyg": "tok-stsyg", "code-mallory": "tok-mallory", "code-bad": "tok-nobody"},
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
        client=client, audit=audit, data=data, abuse=abuse, config=config, fake=fake
    )


def _auth(h) -> str:
    """Mint + install a valid session cookie; return a matching CSRF token."""

    epoch = now_epoch()
    session = signing.issue_session("stsyg", h.config.signing_key, epoch, 3600)
    csrf = signing.issue_csrf("stsyg", h.config.signing_key, epoch, 3600)
    h.client.cookies.set("admin_session", session)
    return csrf


def _call(client, method: str, path: str, body, headers=None):
    if method == "GET":
        return client.get(path)
    return client.post(path, json=body, headers=headers or {})


#: Every guarded admin function, each POST paired with a *valid* body so the
#: body validator can never short-circuit the auth check with a 422.
_GUARDED = [
    ("GET", "/admin/session", None),
    ("GET", "/admin/kill-switch", None),
    ("POST", "/admin/kill-switch", {"enabled": True}),
    ("GET", "/admin/review-queue", None),
    ("POST", "/admin/review-queue/1/action", {"disposition": "approve"}),
    ("GET", "/admin/source-health", None),
    ("POST", "/admin/config-diff", {"candidate": "providers: []"}),
]

_MUTATIONS = [
    ("/admin/kill-switch", {"enabled": True}),
    ("/admin/review-queue/1/action", {"disposition": "approve"}),
    ("/admin/config-diff", {"candidate": "providers: []"}),
]


# --------------------------------------------------------------------------- #
# Unauthenticated access to every guarded function -> 401 + audited.          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method,path,body", _GUARDED)
def test_unauthenticated_access_is_denied_and_audited(harness, method, path, body) -> None:
    resp = _call(harness.client, method, path, body)
    assert resp.status_code == 401
    assert any(r.outcome == "denied" and r.reason == "unauthenticated" for r in harness.audit.rows)


# --------------------------------------------------------------------------- #
# Forged / tampered / expired / wrong-key cookie -> 401 on every endpoint.    #
# --------------------------------------------------------------------------- #


def _tampered(key: str) -> str:
    good = signing.issue_session("stsyg", key, now_epoch(), 3600)
    body, _, sig = good.partition(".")
    flipped = ("A" if body[0] != "A" else "B") + body[1:]
    return f"{flipped}.{sig}"


def _expired(key: str) -> str:
    return signing.issue_session("stsyg", key, now_epoch() - 7200, 3600)


def _wrong_key(_key: str) -> str:
    return signing.issue_session("stsyg", _WRONG_KEY, now_epoch(), 3600)


@pytest.mark.parametrize("cookie_factory", [_tampered, _expired, _wrong_key])
@pytest.mark.parametrize("method,path,body", _GUARDED)
def test_bad_session_cookie_is_denied(harness, cookie_factory, method, path, body) -> None:
    harness.client.cookies.set("admin_session", cookie_factory(harness.config.signing_key))
    resp = _call(harness.client, method, path, body)
    assert resp.status_code == 401
    assert any(r.outcome == "denied" and r.reason == "invalid_cookie" for r in harness.audit.rows)


# --------------------------------------------------------------------------- #
# Missing / invalid CSRF on every mutation -> 403 (with a valid session).     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path,body", _MUTATIONS)
def test_missing_csrf_on_mutation_is_denied(harness, path, body) -> None:
    _auth(harness)  # valid session cookie, but no CSRF header
    resp = harness.client.post(path, json=body)
    assert resp.status_code == 403
    assert any(r.outcome == "denied" and r.reason == "invalid_csrf" for r in harness.audit.rows)


@pytest.mark.parametrize("path,body", _MUTATIONS)
def test_invalid_csrf_on_mutation_is_denied(harness, path, body) -> None:
    _auth(harness)
    resp = harness.client.post(path, json=body, headers={"x-csrf-token": "not.a.valid.csrf"})
    assert resp.status_code == 403
    assert any(r.outcome == "denied" and r.reason == "invalid_csrf" for r in harness.audit.rows)


def test_valid_session_and_csrf_permits_a_mutation(harness) -> None:
    csrf = _auth(harness)
    resp = harness.client.post(
        "/admin/kill-switch", json={"enabled": True}, headers={"x-csrf-token": csrf}
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


# --------------------------------------------------------------------------- #
# OAuth login flow negatives.                                                 #
# --------------------------------------------------------------------------- #


def _login_state(h) -> str:
    h.client.get("/admin/login", follow_redirects=False)
    return h.client.cookies.get("admin_oauth_state")


def test_non_allowlisted_login_denied_no_cookie_audited(harness) -> None:
    state = _login_state(harness)
    resp = harness.client.get(
        f"/admin/callback?code=code-mallory&state={state}", follow_redirects=False
    )
    assert resp.status_code == 403
    assert harness.client.cookies.get("admin_session") is None
    assert any(
        r.reason == "not_allowlisted" and r.actor == "mallory"
        for r in harness.audit.rows
        if r.outcome == "denied"
    )


def test_missing_state_cookie_denied(harness) -> None:
    resp = harness.client.get(
        "/admin/callback?code=code-stsyg&state=whatever", follow_redirects=False
    )
    assert resp.status_code == 401
    assert any(r.reason == "invalid_state" for r in harness.audit.rows)


def test_mismatched_state_denied(harness) -> None:
    _login_state(harness)
    resp = harness.client.get(
        "/admin/callback?code=code-stsyg&state=forged-mismatch", follow_redirects=False
    )
    assert resp.status_code == 401
    assert any(r.reason == "invalid_state" for r in harness.audit.rows)


def test_matching_but_signature_invalid_state_denied(harness) -> None:
    # Set the cookie and the query to the SAME malformed token: states_match()
    # passes, but verify_state() fails the HMAC -> still denied.
    forged = "abc.0000000000000000"
    harness.client.cookies.set("admin_oauth_state", forged)
    resp = harness.client.get(
        f"/admin/callback?code=code-stsyg&state={forged}", follow_redirects=False
    )
    assert resp.status_code == 401
    assert any(r.reason == "invalid_state" for r in harness.audit.rows)


def test_missing_code_is_bad_request(harness) -> None:
    state = _login_state(harness)
    resp = harness.client.get(f"/admin/callback?state={state}", follow_redirects=False)
    assert resp.status_code == 400
    assert any(r.reason == "missing_code" for r in harness.audit.rows)


def test_failed_oauth_exchange_denied(harness) -> None:
    state = _login_state(harness)
    resp = harness.client.get(
        f"/admin/callback?code=code-bad&state={state}", follow_redirects=False
    )
    assert resp.status_code == 401
    assert any(r.reason == "oauth_failed" for r in harness.audit.rows)


def test_allowlisted_login_issues_session_and_audits_success(harness) -> None:
    state = _login_state(harness)
    resp = harness.client.get(
        f"/admin/callback?code=code-stsyg&state={state}", follow_redirects=False
    )
    assert resp.status_code == 302
    assert harness.client.cookies.get("admin_session")
    assert any(
        r.action == "login" and r.outcome == "success" and r.actor == "stsyg"
        for r in harness.audit.rows
    )


# --------------------------------------------------------------------------- #
# Audit no-leak: no secret / token / cookie / signing key / code in any row.  #
# --------------------------------------------------------------------------- #


def test_audit_rows_never_leak_secrets_across_success_and_denied(harness) -> None:
    c = harness.client
    action = next(iter(QUEUE_ACTIONS))

    # Drive a spread of success paths.
    state = _login_state(harness)
    c.get(f"/admin/callback?code=code-stsyg&state={state}", follow_redirects=False)
    csrf = _auth(harness)
    hdr = {"x-csrf-token": csrf}
    c.post("/admin/kill-switch", json={"enabled": True}, headers=hdr)
    c.post("/admin/review-queue/1/action", json={"disposition": action}, headers=hdr)
    c.post("/admin/config-diff", json={"candidate": "providers: []"}, headers=hdr)

    # Drive denials that carry sensitive material nearby (codes, tokens).
    c.cookies.set("admin_session", "")
    c.get("/admin/session")
    state2 = _login_state(harness)
    c.get(f"/admin/callback?code=code-mallory&state={state2}", follow_redirects=False)

    forbidden = {
        harness.config.signing_key,
        harness.config.client_secret,
        harness.config.client_id,
        "code-stsyg",
        "code-mallory",
        "code-bad",
        "tok-stsyg",
        "tok-mallory",
        "tok-nobody",
    }
    marker_substrings = ("secret", "token", "password", "signing_key", "cookie", "authorization")

    assert harness.audit.rows, "expected audit rows to scan"
    for row in harness.audit.rows:
        blob = json.dumps(
            {"actor": row.actor, "reason": row.reason, "context": row.context},
            default=str,
        )
        for needle in forbidden:
            assert needle not in blob, f"audit row leaked '{needle}': {blob}"
        if row.context:
            for key in row.context:
                assert not any(m in str(key).lower() for m in marker_substrings), (
                    f"audit context key '{key}' looks secret-bearing"
                )
