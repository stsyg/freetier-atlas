"""The private admin HTTP routes (F007 slice 4).

All routes live under ``/admin`` (reachable at ``/api/admin`` through the nginx
proxy). The login flow is standard GitHub OAuth with a signed, cookie-bound
``state`` (login CSRF); a successful, *allowlisted* login is issued a stateless
signed session cookie (stdlib HMAC, no session table). Every subsequent admin
request re-verifies that cookie; state-changing actions additionally require a
signed CSRF token bound to the session. Four admin functions sit behind the
guard: the AI kill switch (wired to the existing S2 abuse flag), the
review/contradiction queue, the source-health view, and the validated YAML
config-diff view.

Auditing: every authentication attempt (success and denial) and every mutating
action (and every denial of a protected endpoint) is appended to ``admin_audit``
via the audit store. No secret, token, cookie, or raw OAuth ``code`` is ever
written to an audit row.

Injection seams (``get_admin_config`` / ``get_oauth_client`` /
``get_admin_audit_store`` / ``get_admin_data_store`` / ``get_abuse_store``) are
module-level so tests and the live smoke can substitute an offline OAuth double
and in-memory stores -- real github.com is never networked in tests/smoke
(owner note N1).
"""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from ..adviser.abuse.service import AI_KILL_SWITCH_FLAG
from ..adviser.abuse.store import get_abuse_store
from ..models.vocab import REVIEW_DISPOSITIONS
from ..settings import get_settings
from .audit import get_admin_audit_store
from .config import AdminConfig, get_admin_config
from .configdiff import build_config_diff
from .data import QUEUE_ACTIONS, get_admin_data_store
from .oauth import OAuthError, get_oauth_client
from .service import is_allowlisted, normalize_login
from .signing import (
    STATE_TTL_SECONDS,
    issue_csrf,
    issue_session,
    issue_state,
    read_session,
    states_match,
    verify_csrf,
    verify_state,
)

router = APIRouter(prefix="/admin", tags=["admin"])

#: Cookie / header names.
SESSION_COOKIE = "admin_session"
STATE_COOKIE = "admin_oauth_state"
CSRF_HEADER = "x-csrf-token"

#: OAuth scope: read the authenticated user's public profile (login) only.
_OAUTH_SCOPE = "read:user"


def _now() -> tuple[datetime, int]:
    now = datetime.now(UTC)
    return now, int(now.timestamp())


def _audit(
    *,
    actor: str | None,
    action: str,
    outcome: str,
    reason: str | None = None,
    context: dict[str, Any] | None = None,
    now: datetime,
) -> None:
    """Append one audit row (never raises into the request path)."""

    try:
        get_admin_audit_store().record(
            actor=actor,
            action=action,
            outcome=outcome,
            reason=reason,
            context=context,
            now=now,
        )
    except Exception:  # noqa: BLE001 - auditing must not mask the real outcome
        # A best-effort trail: if the audit sink is unavailable we still enforce
        # the security decision rather than failing open.
        pass


def _require_enabled(config: AdminConfig) -> None:
    if not config.enabled:
        raise HTTPException(status_code=404, detail="Admin surface is disabled.")


def _set_session_cookie(response: Response, value: str, config: AdminConfig) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=config.session_ttl_seconds,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, config: AdminConfig) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=config.cookie_secure, samesite="lax")


def _set_state_cookie(response: Response, value: str, config: AdminConfig) -> None:
    response.set_cookie(
        STATE_COOKIE,
        value,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_state_cookie(response: Response, config: AdminConfig) -> None:
    response.delete_cookie(STATE_COOKIE, path="/", secure=config.cookie_secure, samesite="lax")


def _require_admin(
    request: Request,
    config: AdminConfig,
    action: str,
    now: datetime,
    epoch: int,
) -> str:
    """Return the authenticated admin login or audit the denial and raise 401.

    A missing cookie is ``unauthenticated``; a present-but-invalid cookie
    (tampered / forged / expired) is ``invalid_cookie``.
    """

    token = request.cookies.get(SESSION_COOKIE)
    login = read_session(token, config.signing_key, epoch)
    if login is None:
        _audit(
            actor=None,
            action=action,
            outcome="denied",
            reason="unauthenticated" if not token else "invalid_cookie",
            now=now,
        )
        raise HTTPException(status_code=401, detail="Admin authentication required.")
    return login


def _require_csrf(
    request: Request, login: str, config: AdminConfig, action: str, now: datetime, epoch: int
) -> None:
    """Verify the per-session CSRF token; audit the denial and raise 403 on failure."""

    token = request.headers.get(CSRF_HEADER)
    if not verify_csrf(token, login, config.signing_key, epoch):
        _audit(actor=login, action=action, outcome="denied", reason="invalid_csrf", now=now)
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")


# --------------------------------------------------------------------------- #
# OAuth login flow
# --------------------------------------------------------------------------- #


@router.get("/login")
def login() -> RedirectResponse:
    """Begin the GitHub OAuth flow: set a signed ``state`` cookie and redirect."""

    config = get_admin_config()
    _require_enabled(config)
    now, epoch = _now()
    if not config.configured:
        _audit(actor=None, action="login", outcome="denied", reason="not_configured", now=now)
        raise HTTPException(status_code=503, detail="Admin login is not configured.")

    state = issue_state(config.signing_key, epoch)
    query = urllib.parse.urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": _OAUTH_SCOPE,
            "state": state,
            "allow_signup": "false",
        }
    )
    response = RedirectResponse(f"{config.authorize_url}?{query}", status_code=302)
    _set_state_cookie(response, state, config)
    return response


@router.get("/callback")
def callback(request: Request) -> Response:
    """Complete the OAuth flow: verify state, resolve login, gate on allowlist."""

    config = get_admin_config()
    _require_enabled(config)
    now, epoch = _now()

    query_state = request.query_params.get("state")
    cookie_state = request.cookies.get(STATE_COOKIE)
    state_ok = states_match(query_state, cookie_state) and verify_state(
        query_state, config.signing_key, epoch
    )
    if not state_ok:
        _audit(actor=None, action="login", outcome="denied", reason="invalid_state", now=now)
        response: Response = JSONResponse(
            status_code=401, content={"detail": "Invalid or missing OAuth state."}
        )
        _clear_state_cookie(response, config)
        return response

    code = request.query_params.get("code")
    if not code:
        _audit(actor=None, action="login", outcome="denied", reason="missing_code", now=now)
        response = JSONResponse(status_code=400, content={"detail": "Missing authorization code."})
        _clear_state_cookie(response, config)
        return response

    client = get_oauth_client()
    try:
        access_token = client.exchange_code(code, config)
        github_login = client.fetch_login(access_token, config)
    except OAuthError:
        # The access token / code are intentionally never logged, audited, or
        # returned to the caller.
        _audit(
            actor=None,
            action="login",
            outcome="denied",
            reason="oauth_failed",
            context={"stage": "exchange_or_userinfo"},
            now=now,
        )
        response = JSONResponse(
            status_code=401, content={"detail": "GitHub authentication failed."}
        )
        _clear_state_cookie(response, config)
        return response

    login_norm = normalize_login(github_login)
    if not is_allowlisted(login_norm, config):
        _audit(
            actor=login_norm,
            action="login",
            outcome="denied",
            reason="not_allowlisted",
            now=now,
        )
        response = JSONResponse(
            status_code=403, content={"detail": "This GitHub account is not permitted."}
        )
        _clear_state_cookie(response, config)
        return response

    session_value = issue_session(login_norm, config.signing_key, epoch, config.session_ttl_seconds)
    _audit(
        actor=login_norm,
        action="login",
        outcome="success",
        context={"method": "github_oauth"},
        now=now,
    )
    response = RedirectResponse("/admin", status_code=302)
    _set_session_cookie(response, session_value, config)
    _clear_state_cookie(response, config)
    return response


@router.post("/logout")
def logout(request: Request) -> Response:
    """Clear the admin session cookie (and audit the logout when one existed)."""

    config = get_admin_config()
    _require_enabled(config)
    now, epoch = _now()
    login = read_session(request.cookies.get(SESSION_COOKIE), config.signing_key, epoch)
    if login is not None:
        _audit(actor=login, action="logout", outcome="success", now=now)
    response = JSONResponse(content={"ok": True})
    _clear_session_cookie(response, config)
    return response


@router.get("/session")
def session_info(request: Request) -> dict[str, str]:
    """Return the authenticated admin's login and a fresh CSRF token."""

    config = get_admin_config()
    _require_enabled(config)
    now, epoch = _now()
    login = _require_admin(request, config, "session_view", now, epoch)
    csrf = issue_csrf(login, config.signing_key, epoch, config.session_ttl_seconds)
    return {"login": login, "csrf_token": csrf}


# --------------------------------------------------------------------------- #
# Admin functions (all behind the allowlist + session guard)
# --------------------------------------------------------------------------- #


class KillSwitchToggle(BaseModel):
    enabled: bool


class ReviewAction(BaseModel):
    disposition: str = Field(min_length=1)


class ConfigDiffRequest(BaseModel):
    candidate: str = Field(min_length=1, max_length=200_000)


@router.get("/kill-switch")
def kill_switch_state(request: Request) -> dict[str, bool]:
    """Report the AI kill switch: persisted flag, env override, and effective value."""

    config = get_admin_config()
    _require_enabled(config)
    now, epoch = _now()
    login = _require_admin(request, config, "kill_switch_view", now, epoch)
    del login  # read: identity established, not audited
    persisted = get_abuse_store().get_flag(AI_KILL_SWITCH_FLAG)
    env_override = get_settings().ai_kill_switch
    return {
        "enabled": persisted,
        "env_override": env_override,
        "effective": persisted or env_override,
    }


@router.post("/kill-switch")
def kill_switch_toggle(request: Request, body: KillSwitchToggle) -> dict[str, bool]:
    """Toggle the persisted AI kill switch (the existing S2 abuse flag)."""

    config = get_admin_config()
    _require_enabled(config)
    now, epoch = _now()
    login = _require_admin(request, config, "kill_switch_toggle", now, epoch)
    _require_csrf(request, login, config, "kill_switch_toggle", now, epoch)
    get_abuse_store().set_flag(AI_KILL_SWITCH_FLAG, body.enabled, now)
    _audit(
        actor=login,
        action="kill_switch_toggle",
        outcome="success",
        context={"enabled": body.enabled},
        now=now,
    )
    return {"enabled": body.enabled}


@router.get("/review-queue")
def review_queue(request: Request) -> dict[str, Any]:
    """List review/contradiction items (optionally filtered by ``disposition``)."""

    config = get_admin_config()
    _require_enabled(config)
    now, epoch = _now()
    login = _require_admin(request, config, "review_queue_view", now, epoch)
    del login
    disposition = request.query_params.get("disposition")
    if disposition is not None and disposition not in REVIEW_DISPOSITIONS:
        raise HTTPException(status_code=422, detail="Unknown disposition filter.")
    items = get_admin_data_store().list_review_queue(disposition)
    return {
        "items": [
            {
                "id": item.id,
                "reason": item.reason,
                "recommended_action": item.recommended_action,
                "admin_disposition": item.admin_disposition,
                "evidence_conflict": item.evidence_conflict,
                "candidate_facts": item.candidate_facts,
                "offer_id": item.offer_id,
                "scan_run_id": item.scan_run_id,
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ],
        "valid_actions": list(QUEUE_ACTIONS),
    }


@router.post("/review-queue/{item_id}/action")
def review_queue_action(
    request: Request,
    item_id: int,
    body: ReviewAction,
) -> dict[str, Any]:
    """Advance a review item's disposition (approve / reject / defer)."""

    config = get_admin_config()
    _require_enabled(config)
    now, epoch = _now()
    login = _require_admin(request, config, "review_action", now, epoch)
    _require_csrf(request, login, config, "review_action", now, epoch)
    if body.disposition not in QUEUE_ACTIONS:
        _audit(
            actor=login,
            action="review_action",
            outcome="denied",
            reason="invalid_disposition",
            context={"item_id": item_id, "disposition": body.disposition},
            now=now,
        )
        raise HTTPException(
            status_code=422,
            detail=f"disposition must be one of {', '.join(QUEUE_ACTIONS)}",
        )
    updated = get_admin_data_store().set_review_disposition(item_id, body.disposition, now)
    if not updated:
        _audit(
            actor=login,
            action="review_action",
            outcome="denied",
            reason="item_not_found",
            context={"item_id": item_id},
            now=now,
        )
        raise HTTPException(status_code=404, detail="Review item not found.")
    _audit(
        actor=login,
        action="review_action",
        outcome="success",
        context={"item_id": item_id, "disposition": body.disposition},
        now=now,
    )
    return {"id": item_id, "disposition": body.disposition}


@router.get("/source-health")
def source_health(request: Request) -> dict[str, Any]:
    """Read-only source health from existing source / scan_run / snapshot tables."""

    config = get_admin_config()
    _require_enabled(config)
    now, epoch = _now()
    login = _require_admin(request, config, "source_health_view", now, epoch)
    del login
    rows = get_admin_data_store().source_health()
    return {
        "sources": [
            {
                "source_id": row.source_id,
                "slug": row.slug,
                "adapter_type": row.adapter_type,
                "official": row.official,
                "enabled": row.enabled,
                "health": row.health,
                "endpoint": row.endpoint,
                "last_scan_status": row.last_scan_status,
                "last_scan_finished_at": (
                    row.last_scan_finished_at.isoformat()
                    if row.last_scan_finished_at is not None
                    else None
                ),
                "last_errors_count": row.last_errors_count,
                "last_snapshot_fetched_at": (
                    row.last_snapshot_fetched_at.isoformat()
                    if row.last_snapshot_fetched_at is not None
                    else None
                ),
            }
            for row in rows
        ]
    }


@router.post("/config-diff")
def config_diff(request: Request, body: ConfigDiffRequest) -> dict[str, Any]:
    """Validate a candidate config and diff it against the running config."""

    config = get_admin_config()
    _require_enabled(config)
    now, epoch = _now()
    login = _require_admin(request, config, "config_diff", now, epoch)
    _require_csrf(request, login, config, "config_diff", now, epoch)
    committed_path = get_settings().llm_config_path
    result = build_config_diff(body.candidate, committed_path)
    _audit(
        actor=login,
        action="config_diff",
        outcome="success",
        context={"valid": result.valid, "target": result.target},
        now=now,
    )
    return {
        "target": result.target,
        "valid": result.valid,
        "problems": result.problems,
        "diff": result.diff,
        "committed_present": result.committed_present,
    }


__all__ = ["router"]
