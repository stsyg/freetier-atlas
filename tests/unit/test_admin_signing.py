"""Unit tests for the stateless signed-token core + allowlist gate (F007 S4).

These cover owner decision Q6 (stateless signed cookie, stdlib HMAC, no session
table) in isolation: a forged, tampered, expired, or wrong-kind token must never
verify, signature comparison is constant-time, and the allowlist gate (F007
acceptance step 3) is case-insensitive and rejects empty logins.
"""

from __future__ import annotations

from app.admin import signing
from app.admin.service import is_allowlisted, normalize_login

from tests.support.admin import ADMIN_SIGNING_KEY, make_admin_config

KEY = ADMIN_SIGNING_KEY
NOW = 1_800_000_000


# --- session cookie --------------------------------------------------------- #


def test_session_round_trip_returns_login() -> None:
    token = signing.issue_session("stsyg", KEY, NOW, ttl_seconds=3600)
    assert signing.read_session(token, KEY, NOW) == "stsyg"
    # still valid just before expiry
    assert signing.read_session(token, KEY, NOW + 3599) == "stsyg"


def test_session_expired_returns_none() -> None:
    token = signing.issue_session("stsyg", KEY, NOW, ttl_seconds=10)
    assert signing.read_session(token, KEY, NOW + 11) is None


def test_session_tampered_body_returns_none() -> None:
    token = signing.issue_session("stsyg", KEY, NOW, ttl_seconds=3600)
    body, _, sig = token.partition(".")
    # Flip a character in the payload; the signature no longer matches.
    forged = ("A" + body[1:] if body[0] != "A" else "B" + body[1:]) + "." + sig
    assert signing.read_session(forged, KEY, NOW) is None


def test_session_tampered_signature_returns_none() -> None:
    token = signing.issue_session("stsyg", KEY, NOW, ttl_seconds=3600)
    body, _, sig = token.partition(".")
    forged = body + "." + ("0" * len(sig))
    assert signing.read_session(forged, KEY, NOW) is None


def test_session_wrong_key_returns_none() -> None:
    token = signing.issue_session("stsyg", KEY, NOW, ttl_seconds=3600)
    assert signing.read_session(token, "another-key", NOW) is None


def test_session_none_and_malformed_return_none() -> None:
    assert signing.read_session(None, KEY, NOW) is None
    assert signing.read_session("", KEY, NOW) is None
    assert signing.read_session("no-dot-here", KEY, NOW) is None
    assert signing.read_session("not-base64.deadbeef", KEY, NOW) is None


def test_csrf_token_is_not_accepted_as_session() -> None:
    csrf = signing.issue_csrf("stsyg", KEY, NOW, ttl_seconds=3600)
    assert signing.read_session(csrf, KEY, NOW) is None


# --- OAuth state (login CSRF) ---------------------------------------------- #


def test_state_round_trip_and_expiry() -> None:
    token = signing.issue_state(KEY, NOW, ttl_seconds=600)
    assert signing.verify_state(token, KEY, NOW) is True
    assert signing.verify_state(token, KEY, NOW + 601) is False


def test_state_tampered_returns_false() -> None:
    token = signing.issue_state(KEY, NOW, ttl_seconds=600)
    body, _, sig = token.partition(".")
    assert signing.verify_state(body + "." + "0" * len(sig), KEY, NOW) is False


def test_session_token_is_not_accepted_as_state() -> None:
    session = signing.issue_session("stsyg", KEY, NOW, ttl_seconds=600)
    assert signing.verify_state(session, KEY, NOW) is False


def test_states_match_is_exact_and_rejects_missing() -> None:
    assert signing.states_match("abc", "abc") is True
    assert signing.states_match("abc", "abd") is False
    assert signing.states_match(None, "abc") is False
    assert signing.states_match("abc", None) is False
    assert signing.states_match("", "") is False


# --- CSRF token (admin mutations) ------------------------------------------ #


def test_csrf_round_trip_bound_to_login() -> None:
    token = signing.issue_csrf("stsyg", KEY, NOW, ttl_seconds=3600)
    assert signing.verify_csrf(token, "stsyg", KEY, NOW) is True


def test_csrf_rejected_for_other_login() -> None:
    token = signing.issue_csrf("stsyg", KEY, NOW, ttl_seconds=3600)
    assert signing.verify_csrf(token, "mallory", KEY, NOW) is False


def test_csrf_expired_and_missing_return_false() -> None:
    token = signing.issue_csrf("stsyg", KEY, NOW, ttl_seconds=10)
    assert signing.verify_csrf(token, "stsyg", KEY, NOW + 11) is False
    assert signing.verify_csrf(None, "stsyg", KEY, NOW) is False


# --- allowlist gate --------------------------------------------------------- #


def test_allowlist_is_case_insensitive() -> None:
    config = make_admin_config(allowlist=frozenset({"stsyg"}))
    assert is_allowlisted("stsyg", config) is True
    assert is_allowlisted("STSYG", config) is True
    assert is_allowlisted("  Stsyg  ", config) is True


def test_allowlist_rejects_unknown_and_empty() -> None:
    config = make_admin_config(allowlist=frozenset({"stsyg"}))
    assert is_allowlisted("mallory", config) is False
    assert is_allowlisted("", config) is False
    assert is_allowlisted("   ", config) is False


def test_normalize_login_lowercases_and_trims() -> None:
    assert normalize_login("  StSyg ") == "stsyg"
