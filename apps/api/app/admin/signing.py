"""Stateless signed tokens for the admin surface (stdlib HMAC only).

Honouring owner decision Q6 (stateless signed cookie, no session table, no JWT
library), this module builds and verifies three kinds of tamper-evident tokens
using only :mod:`hmac`, :mod:`hashlib`, :mod:`base64`, :mod:`json`,
:mod:`secrets`:

* the **session cookie** carrying the authenticated GitHub login plus issued /
  expiry timestamps,
* the OAuth **state** token (login-flow CSRF), and
* the per-session **CSRF token** protecting state-changing admin actions.

Every token has the form ``<base64url(json payload)>.<hex HMAC-SHA256>``. The
signature is compared in constant time (:func:`hmac.compare_digest`) and the
embedded ``exp`` is enforced, so a forged, tampered, or expired token verifies
to ``None`` / ``False`` and the caller denies. Signing keys and payloads never
contain a secret credential.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets

# Token "kind" discriminators, embedded in the payload so a token minted for one
# purpose cannot be replayed as another.
_KIND_SESSION = "session"
_KIND_STATE = "state"
_KIND_CSRF = "csrf"

#: Default lifetime (seconds) of the short-lived OAuth ``state`` token.
STATE_TTL_SECONDS = 600


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _signature(body: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()


def _encode(payload: dict, key: str) -> str:
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{body}.{_signature(body, key)}"


def _decode(token: str | None, key: str, now_epoch: int) -> dict | None:
    """Return the payload of a valid, unexpired token, else ``None``.

    Verifies the HMAC signature in constant time and enforces ``exp`` when
    present. Any structural problem (missing dot, bad base64, non-JSON,
    non-object) yields ``None`` rather than raising.
    """

    if not token or "." not in token:
        return None
    body, _, signature = token.partition(".")
    expected = _signature(body, key)
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if exp is not None:
        try:
            if int(exp) < now_epoch:
                return None
        except (TypeError, ValueError):
            return None
    return payload


# ---- session cookie -------------------------------------------------------- #


def issue_session(login: str, key: str, now_epoch: int, ttl_seconds: int) -> str:
    """Mint a signed session cookie value for ``login``."""

    return _encode(
        {
            "kind": _KIND_SESSION,
            "sub": login,
            "iat": now_epoch,
            "exp": now_epoch + ttl_seconds,
        },
        key,
    )


def read_session(token: str | None, key: str, now_epoch: int) -> str | None:
    """Return the authenticated login from a valid session cookie, else ``None``."""

    payload = _decode(token, key, now_epoch)
    if payload is None or payload.get("kind") != _KIND_SESSION:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) and sub else None


# ---- OAuth state (login CSRF) --------------------------------------------- #


def issue_state(key: str, now_epoch: int, ttl_seconds: int = STATE_TTL_SECONDS) -> str:
    """Mint a signed, single-flow OAuth ``state`` token with a random nonce."""

    return _encode(
        {
            "kind": _KIND_STATE,
            "nonce": secrets.token_urlsafe(16),
            "exp": now_epoch + ttl_seconds,
        },
        key,
    )


def verify_state(token: str | None, key: str, now_epoch: int) -> bool:
    """Return ``True`` when ``token`` is a valid, unexpired state token."""

    payload = _decode(token, key, now_epoch)
    return bool(payload and payload.get("kind") == _KIND_STATE)


def states_match(query_state: str | None, cookie_state: str | None) -> bool:
    """Constant-time equality of the callback query state and the cookie state."""

    if not query_state or not cookie_state:
        return False
    return hmac.compare_digest(query_state, cookie_state)


# ---- CSRF token (admin mutations) ----------------------------------------- #


def issue_csrf(login: str, key: str, now_epoch: int, ttl_seconds: int) -> str:
    """Mint a signed CSRF token bound to the session ``login``."""

    return _encode(
        {
            "kind": _KIND_CSRF,
            "login": login,
            "exp": now_epoch + ttl_seconds,
        },
        key,
    )


def verify_csrf(token: str | None, login: str, key: str, now_epoch: int) -> bool:
    """Return ``True`` when ``token`` is a valid CSRF token bound to ``login``."""

    payload = _decode(token, key, now_epoch)
    if payload is None or payload.get("kind") != _KIND_CSRF:
        return False
    bound = payload.get("login")
    return isinstance(bound, str) and hmac.compare_digest(bound, login)


__all__ = [
    "STATE_TTL_SECONDS",
    "issue_session",
    "read_session",
    "issue_state",
    "verify_state",
    "states_match",
    "issue_csrf",
    "verify_csrf",
]
