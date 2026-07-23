"""Concrete source adapters built on the :mod:`app.ingest.base` contract.

Each adapter turns one *official* source shape into candidate-only facts and
reaches the network solely through the injected
:class:`~app.ingest.fetch.Fetcher`. Provider-specific knowledge stays behind the
adapter boundary (and, for HTML, inside a declarative extraction profile).
"""

from __future__ import annotations

from .html import (
    HTML_EXTRACTION_PROFILES,
    HtmlColumn,
    HtmlDocAdapter,
    HtmlExtractionProfile,
    UnknownProfileError,
    resolve_profile,
)
from .rss import RssFeedAdapter

__all__ = (
    "RssFeedAdapter",
    "HtmlDocAdapter",
    "HtmlColumn",
    "HtmlExtractionProfile",
    "HTML_EXTRACTION_PROFILES",
    "resolve_profile",
    "UnknownProfileError",
)
