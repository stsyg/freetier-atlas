"""Immutable admin configuration, resolved once from :class:`Settings`.

:class:`AdminConfig` is a frozen snapshot of the admin-relevant settings so the
rest of the package never reaches back into global settings and is trivially
constructed in tests. Secret *values* (the OAuth client secret and the cookie
signing key) live only in the environment; they are read here but never logged,
never audited, and never returned to a client.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..settings import Settings, get_settings


@dataclass(frozen=True)
class AdminConfig:
    """A frozen snapshot of the admin surface's configuration."""

    enabled: bool
    client_id: str
    client_secret: str
    signing_key: str
    allowlist: frozenset[str]
    authorize_url: str
    token_url: str
    user_url: str
    base_url: str
    cookie_secure: bool
    session_ttl_seconds: int

    @property
    def redirect_uri(self) -> str:
        """The OAuth callback URL registered with the GitHub OAuth app."""

        return f"{self.base_url.rstrip('/')}/api/admin/callback"

    @property
    def configured(self) -> bool:
        """True when both OAuth credentials are present (login can proceed)."""

        return bool(self.client_id and self.client_secret)


def _parse_allowlist(raw: str) -> frozenset[str]:
    """Parse the comma-separated allowlist into a set of lowercased logins."""

    return frozenset(entry.strip().lower() for entry in raw.split(",") if entry.strip())


def build_admin_config(settings: Settings) -> AdminConfig:
    """Construct an :class:`AdminConfig` from a :class:`Settings` instance."""

    return AdminConfig(
        enabled=settings.admin_enabled,
        client_id=settings.admin_github_client_id,
        client_secret=settings.admin_github_client_secret,
        signing_key=settings.admin_cookie_signing_key,
        allowlist=_parse_allowlist(settings.admin_allowlist),
        authorize_url=settings.admin_oauth_authorize_url,
        token_url=settings.admin_oauth_token_url,
        user_url=settings.admin_oauth_user_url,
        base_url=settings.admin_base_url,
        cookie_secure=settings.admin_cookie_secure,
        session_ttl_seconds=settings.admin_session_ttl_seconds,
    )


def get_admin_config() -> AdminConfig:
    """Return the admin configuration built from the process settings.

    This is the injection seam the router depends on; tests override it (or the
    underlying settings) to exercise the flow without real credentials.
    """

    return build_admin_config(get_settings())


__all__ = ["AdminConfig", "build_admin_config", "get_admin_config"]
