"""Per-provider extraction-profile modules and their registration seam (F008 S3).

Provider-specific extraction knowledge is **data**, and each provider owns its
own module in this package. A provider slice adds exactly one file --
``profiles/<provider>.py`` -- which registers its profiles here; it never edits a
shared registry dict, a shared ``__init__`` list, or any other provider's file.
That is what makes six provider slices safe to build **concurrently**: their
footprints are disjoint by construction, so they cannot conflict at merge time.

The seam has three parts:

* **Registration functions** -- :func:`register_html_profile`,
  :func:`register_json_profile` and :func:`register_mcp_profile` write into the
  adapters' existing registries (``HTML_EXTRACTION_PROFILES``,
  ``JSON_EXTRACTION_PROFILES``, ``MCP_PROFILES``), so ``resolve_profile`` and
  friends keep working unchanged.
* **Conflict detection** -- registering a name that is already taken raises
  :class:`ProfileConflictError` rather than silently shadowing it. With six
  concurrent slices a duplicated profile name is a real hazard, and a loud
  failure is the honest outcome ("unknown is better than guessed").
* **Auto-discovery** -- :func:`load_provider_profiles` imports every module in
  this package via :mod:`pkgutil`, so *dropping a file in is the whole
  integration step*. It is invoked once from :mod:`app.ingest.adapters` at
  import time and is idempotent.

Registration is deliberately *additive only*: nothing here mutates or removes a
profile another module registered, and re-importing a provider module (or
calling :func:`load_provider_profiles` again) is a no-op because a module body is
executed only once per interpreter.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Sequence
from typing import Any

from .._json import JsonExtractionProfile
from ..html import HTML_EXTRACTION_PROFILES, HtmlExtractionProfile
from ..mcp import MCP_PROFILES, McpSourceProfile
from ..structured import JSON_EXTRACTION_PROFILES


class ProfileConflictError(ValueError):
    """Raised when a profile name is registered twice.

    A silent overwrite would let one provider slice change another provider's
    extraction behaviour by accident, so a collision fails loudly instead.
    """


def _register(
    registry: dict[str, Any],
    profile: Any,
    name: str,
    kind: str,
    *,
    replace: bool,
) -> Any:
    existing = registry.get(name)
    if existing is not None and not replace:
        if existing is profile:
            return profile  # idempotent re-registration of the identical object
        raise ProfileConflictError(
            f"A {kind} extraction profile named '{name}' is already registered. "
            "Profile names must be unique across providers; rename yours "
            "(convention: '<provider>_<document>')."
        )
    registry[name] = profile
    return profile


def register_html_profile(
    profile: HtmlExtractionProfile, *, replace: bool = False
) -> HtmlExtractionProfile:
    """Register ``profile`` under its own name in ``HTML_EXTRACTION_PROFILES``."""

    _register(HTML_EXTRACTION_PROFILES, profile, profile.name, "HTML", replace=replace)
    return profile


def register_json_profile(
    profile: JsonExtractionProfile, *, replace: bool = False
) -> JsonExtractionProfile:
    """Register ``profile`` under its own name in ``JSON_EXTRACTION_PROFILES``."""

    _register(JSON_EXTRACTION_PROFILES, profile, profile.name, "structured/JSON", replace=replace)
    return profile


def register_mcp_profile(profile: McpSourceProfile, *, replace: bool = False) -> McpSourceProfile:
    """Register ``profile`` under its own name in ``MCP_PROFILES``."""

    _register(MCP_PROFILES, profile, profile.name, "MCP", replace=replace)
    return profile


def provider_profile_modules() -> tuple[str, ...]:
    """Return the sorted names of the per-provider modules in this package."""

    return tuple(sorted(info.name for info in pkgutil.iter_modules(__path__)))


def load_provider_profiles() -> tuple[str, ...]:
    """Import every per-provider profile module, registering its profiles.

    Idempotent: Python executes a module body only once, so repeated calls (or a
    provider module imported directly) register nothing twice. Returns the fully
    qualified module names that were imported.
    """

    loaded: list[str] = []
    for name in provider_profile_modules():
        importlib.import_module(f"{__name__}.{name}")
        loaded.append(f"{__name__}.{name}")
    return tuple(loaded)


def registered_profile_names() -> dict[str, tuple[str, ...]]:
    """Introspection helper: the registered profile names by registry kind."""

    return {
        "html": tuple(sorted(HTML_EXTRACTION_PROFILES)),
        "json": tuple(sorted(JSON_EXTRACTION_PROFILES)),
        "mcp": tuple(sorted(MCP_PROFILES)),
    }


__all__: Sequence[str] = (
    "ProfileConflictError",
    "register_html_profile",
    "register_json_profile",
    "register_mcp_profile",
    "load_provider_profiles",
    "provider_profile_modules",
    "registered_profile_names",
)
