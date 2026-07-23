"""Concrete source adapters built on the :mod:`app.ingest.base` contract.

Each adapter turns one *official* source shape into candidate-only facts and
reaches the network solely through the injected
:class:`~app.ingest.fetch.Fetcher`. Provider-specific knowledge stays behind the
adapter boundary (and, for HTML, inside a declarative extraction profile).
"""

from __future__ import annotations

from ._json import JsonExtractionProfile, JsonField
from .html import (
    HTML_EXTRACTION_PROFILES,
    HtmlColumn,
    HtmlDocAdapter,
    HtmlExtractionProfile,
    UnknownProfileError,
    resolve_profile,
)
from .mcp import (
    MCP_PROFILES,
    DisallowedCapabilityError,
    McpClient,
    McpDisabledError,
    McpSourceProfile,
    McpToolAdapter,
    McpToolResult,
    OfflineMcpClient,
    UnknownMcpProfileError,
    resolve_mcp_profile,
)
from .rss import RssFeedAdapter
from .structured import (
    JSON_EXTRACTION_PROFILES,
    StructuredApiAdapter,
    UnknownJsonProfileError,
    resolve_json_profile,
)

__all__ = (
    "RssFeedAdapter",
    "HtmlDocAdapter",
    "HtmlColumn",
    "HtmlExtractionProfile",
    "HTML_EXTRACTION_PROFILES",
    "resolve_profile",
    "UnknownProfileError",
    # structured-API adapter
    "StructuredApiAdapter",
    "JSON_EXTRACTION_PROFILES",
    "resolve_json_profile",
    "UnknownJsonProfileError",
    # shared JSON extraction primitives
    "JsonExtractionProfile",
    "JsonField",
    # MCP adapter
    "McpToolAdapter",
    "McpClient",
    "McpToolResult",
    "OfflineMcpClient",
    "McpSourceProfile",
    "MCP_PROFILES",
    "resolve_mcp_profile",
    "UnknownMcpProfileError",
    "DisallowedCapabilityError",
    "McpDisabledError",
)
