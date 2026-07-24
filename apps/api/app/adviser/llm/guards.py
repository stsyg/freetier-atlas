"""Configuration-time egress guards for provider ``base_url`` values.

A provider's ``base_url`` never comes from user input -- it is supplied by the
operator through configuration (a ``*_base_url_env`` environment variable named
in the YAML). Even so, we pass it through the *same* pure scheme guard the
ingestion pipeline uses (:func:`app.ingest.fetch.check_scheme`) so a
misconfigured endpoint (e.g. ``file://`` or an embedded-credential URL) is
rejected loudly at construction rather than silently trusted.

These checks are intentionally **network-free**: no DNS resolution and no socket
use (a bare host with no configured egress cannot be resolved in CI). Full
per-request SSRF enforcement -- DNS + private/metadata IP blocking via
:func:`app.ingest.fetch.address_block_reason` -- belongs with the real network
transport, which is not built in this slice (the real adapters do no I/O).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.ingest.fetch import DEFAULT_ALLOWED_SCHEMES, FetchError, check_scheme

#: Schemes an LLM provider endpoint may use. ``http`` is permitted in addition
#: to ``https`` because a ``LOCAL`` provider (e.g. Ollama) is commonly reached
#: over plain HTTP on a loopback/private address the operator controls.
ALLOWED_PROVIDER_SCHEMES: frozenset[str] = DEFAULT_ALLOWED_SCHEMES | {"http"}


class ProviderBaseUrlError(ValueError):
    """A configured provider ``base_url`` is not a safe absolute endpoint."""


def guard_base_url(base_url: str) -> str:
    """Validate a configured provider ``base_url`` (no DNS, no network).

    Rejects a disallowed scheme, an embedded credential (``user:pass@host``),
    or a missing host. Returns the URL unchanged when it is a well-formed
    ``http``/``https`` absolute endpoint.
    """

    if not isinstance(base_url, str) or not base_url.strip():
        raise ProviderBaseUrlError("provider base_url must be a non-empty string")

    candidate = base_url.strip()
    try:
        check_scheme(candidate, ALLOWED_PROVIDER_SCHEMES)
    except FetchError as exc:  # DisallowedSchemeError is a FetchError
        raise ProviderBaseUrlError(str(exc)) from exc

    parts = urlsplit(candidate)
    if not parts.hostname:
        raise ProviderBaseUrlError("provider base_url must include a host")
    if parts.username or parts.password:
        raise ProviderBaseUrlError("provider base_url must not embed credentials")

    return candidate


__all__ = ["ALLOWED_PROVIDER_SCHEMES", "ProviderBaseUrlError", "guard_base_url"]
