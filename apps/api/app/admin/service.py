"""Pure, FastAPI-free admin decision helpers (allowlist + login hygiene).

Kept deliberately small and side-effect free so the allowlist gate -- the heart
of F007 acceptance step 3 (only an allowlisted GitHub admin may use the admin
functions) -- is unit-tested in isolation.
"""

from __future__ import annotations

from .config import AdminConfig


def normalize_login(login: str) -> str:
    """Return the GitHub login in the canonical (lowercased, trimmed) form."""

    return login.strip().lower()


def is_allowlisted(login: str, config: AdminConfig) -> bool:
    """Return ``True`` when ``login`` is on the configured admin allowlist.

    The comparison is case-insensitive (GitHub logins are case-insensitive) and
    an empty / whitespace login is never allowlisted.
    """

    candidate = normalize_login(login)
    if not candidate:
        return False
    return candidate in config.allowlist


__all__ = ["normalize_login", "is_allowlisted"]
