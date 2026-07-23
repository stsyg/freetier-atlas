"""Shared, side-effect-free helpers for the source adapters.

Kept tiny and dependency-free so both the RSS and HTML adapters can normalise
values identically without importing an HTTP client or any provider-specific
logic.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Machine-readable truthy / falsy spellings an official source might use. Anything
# outside these sets is treated as UNKNOWN (``None``) -- never guessed either way.
_TRUE_TOKENS: frozenset[str] = frozenset({"true", "yes", "required", "y", "1"})
_FALSE_TOKENS: frozenset[str] = frozenset({"false", "no", "not required", "none", "n", "0"})


def host(url: str) -> str:
    """Return the lowercased hostname of ``url`` (empty string if absent)."""

    return (urlsplit(url).hostname or "").lower()


def normspace(value: str) -> str:
    """Collapse all runs of whitespace to single spaces and strip the ends."""

    return " ".join(value.split())


def to_bool(value: str | None) -> bool | None:
    """Coerce a source string to a bool, or ``None`` when it is not unambiguous.

    ``None`` in / ``None`` out. An unrecognised token yields ``None`` (UNKNOWN)
    rather than a guessed boolean -- "unknown is better than guessed".
    """

    if value is None:
        return None
    token = normspace(value).lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return None


__all__ = ("host", "normspace", "to_bool")
