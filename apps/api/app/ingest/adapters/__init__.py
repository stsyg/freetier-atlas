"""Concrete source adapters built on the :mod:`app.ingest.base` contract.

Each adapter turns one *official* source shape into candidate-only facts and
reaches the network solely through the injected
:class:`~app.ingest.fetch.Fetcher`. Provider-specific knowledge stays behind the
adapter boundary (and, for HTML, inside a declarative extraction profile).

Provider-specific *profiles* live one-module-per-provider under
:mod:`app.ingest.adapters.profiles` and are auto-discovered at import time by
:func:`~app.ingest.adapters.profiles.load_provider_profiles` (F008 S3). Adding a
provider is therefore a single new file: no shared registry dict, and no line in
this module, needs editing.
"""

from __future__ import annotations

from ._json import JsonExtractionProfile, JsonField
from .html import (
    HTML_EXTRACTION_PROFILES,
    HtmlColumn,
    HtmlDocAdapter,
    HtmlExtractionProfile,
    HtmlMatrixRow,
    HtmlTextAssertion,
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
from .profiles import (
    ProfileConflictError,
    load_provider_profiles,
    register_html_profile,
    register_json_profile,
    register_mcp_profile,
    registered_profile_names,
)
from .rss import RssFeedAdapter
from .structured import (
    JSON_EXTRACTION_PROFILES,
    StructuredApiAdapter,
    UnknownJsonProfileError,
    resolve_json_profile,
)

# Import every per-provider profile module so its profiles are registered before
# any adapter resolves a profile name. Idempotent (module bodies run once).
load_provider_profiles()

__all__ = (
    "RssFeedAdapter",
    "HtmlDocAdapter",
    "HtmlColumn",
    "HtmlMatrixRow",
    "HtmlTextAssertion",
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
    # per-provider profile registration seam (F008 S3)
    "ProfileConflictError",
    "register_html_profile",
    "register_json_profile",
    "register_mcp_profile",
    "load_provider_profiles",
    "registered_profile_names",
)
