"""GitHub OAuth web-flow client behind a narrow, injectable seam.

The :class:`GitHubOAuthClient` protocol is the only shape the router depends on:
exchange an authorization ``code`` for an access token, then resolve that token
to a GitHub login. Two implementations exist:

* :class:`UrllibGitHubOAuthClient` -- the real client, built on the Python
  standard library (:mod:`urllib.request`) so no OAuth SDK / HTTP dependency is
  added (owner decision Q9). It POSTs to the token endpoint and GETs the user
  endpoint with short timeouts and never logs the client secret or the access
  token.
* :class:`FakeGitHubOAuthClient` -- a deterministic, offline double injected by
  the tests and the live smoke (owner note N1: real github.com is never
  networked in CI / smoke). It maps codes to tokens and tokens to logins from
  in-memory tables and can be told to fail, so the negative-security matrix
  (bad exchange, bad userinfo) is exercised without a network.

Neither implementation ever emits a secret into logs or return values.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol, runtime_checkable

from .config import AdminConfig

#: Network timeout (seconds) for the real OAuth HTTP calls.
_HTTP_TIMEOUT_SECONDS = 10


class OAuthError(Exception):
    """A recoverable OAuth failure. The message is safe to audit (no secrets)."""


@runtime_checkable
class GitHubOAuthClient(Protocol):
    """The narrow OAuth seam used by the admin router."""

    def exchange_code(self, code: str, config: AdminConfig) -> str:
        """Exchange an authorization ``code`` for an access token."""
        ...

    def fetch_login(self, access_token: str, config: AdminConfig) -> str:
        """Resolve an access token to the authenticated GitHub login."""
        ...


class UrllibGitHubOAuthClient:
    """Real GitHub OAuth client built on :mod:`urllib` (no new dependency)."""

    def exchange_code(self, code: str, config: AdminConfig) -> str:
        payload = urllib.parse.urlencode(
            {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                "redirect_uri": config.redirect_uri,
            }
        ).encode("ascii")
        request = urllib.request.Request(  # noqa: S310 - fixed, config-controlled URL
            config.token_url,
            data=payload,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "freetier-atlas-admin",
            },
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed, config-controlled URL
                request, timeout=_HTTP_TIMEOUT_SECONDS
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            raise OAuthError("token exchange request failed") from exc
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            # ``body`` may carry an ``error`` code; do not echo it verbatim in
            # case a provider ever reflects the secret, keep the message generic.
            raise OAuthError("token exchange did not return an access token")
        return token

    def fetch_login(self, access_token: str, config: AdminConfig) -> str:
        request = urllib.request.Request(  # noqa: S310 - fixed, config-controlled URL
            config.user_url,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "freetier-atlas-admin",
            },
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed, config-controlled URL
                request, timeout=_HTTP_TIMEOUT_SECONDS
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            raise OAuthError("userinfo request failed") from exc
        login = body.get("login") if isinstance(body, dict) else None
        if not isinstance(login, str) or not login:
            raise OAuthError("userinfo did not return a login")
        return login


class FakeGitHubOAuthClient:
    """Deterministic, offline OAuth double for tests and the live smoke.

    ``codes`` maps an authorization code to an access token; ``logins`` maps an
    access token to a GitHub login. A code / token absent from its table raises
    :class:`OAuthError`, so callers can drive the failure branches without any
    network access.
    """

    def __init__(self, codes: dict[str, str], logins: dict[str, str]) -> None:
        self._codes = dict(codes)
        self._logins = dict(logins)

    def exchange_code(self, code: str, config: AdminConfig) -> str:
        token = self._codes.get(code)
        if token is None:
            raise OAuthError("unknown authorization code")
        return token

    def fetch_login(self, access_token: str, config: AdminConfig) -> str:
        login = self._logins.get(access_token)
        if login is None:
            raise OAuthError("unknown access token")
        return login


def get_oauth_client() -> GitHubOAuthClient:
    """Return the real OAuth client. Tests / smoke override this seam."""

    return UrllibGitHubOAuthClient()


__all__ = [
    "OAuthError",
    "GitHubOAuthClient",
    "UrllibGitHubOAuthClient",
    "FakeGitHubOAuthClient",
    "get_oauth_client",
]
