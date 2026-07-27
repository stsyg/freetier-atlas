"""Shared test helpers for the private admin surface (F007 slice 4)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.admin.config import AdminConfig

#: Fixed signing key for deterministic token tests (non-secret test value).
ADMIN_SIGNING_KEY = "test-admin-signing-key"  # pragma: allowlist secret


def make_admin_config(
    *,
    enabled: bool = True,
    client_id: str = "test-client-id",
    client_secret: str = "test-client-secret",  # pragma: allowlist secret
    signing_key: str = ADMIN_SIGNING_KEY,
    allowlist: frozenset[str] | None = None,
    authorize_url: str = "https://github.test/login/oauth/authorize",
    token_url: str = "https://github.test/login/oauth/access_token",  # pragma: allowlist secret
    user_url: str = "https://api.github.test/user",
    base_url: str = "http://localhost:8080",
    cookie_secure: bool = False,
    session_ttl_seconds: int = 3600,
) -> AdminConfig:
    """Build a deterministic :class:`AdminConfig` for tests.

    ``cookie_secure`` defaults to ``False`` so the httpx test client (which talks
    plain HTTP) retains and resends the signed cookies.
    """

    return AdminConfig(
        enabled=enabled,
        client_id=client_id,
        client_secret=client_secret,
        signing_key=signing_key,
        allowlist=allowlist if allowlist is not None else frozenset({"stsyg"}),
        authorize_url=authorize_url,
        token_url=token_url,
        user_url=user_url,
        base_url=base_url,
        cookie_secure=cookie_secure,
        session_ttl_seconds=session_ttl_seconds,
    )


def now_epoch() -> int:
    """Return the current UNIX epoch (seconds) as the router computes it."""

    return int(datetime.now(UTC).timestamp())
