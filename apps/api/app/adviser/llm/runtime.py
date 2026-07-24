"""Runtime wiring for the adviser LLM layer: config -> limits + registry.

This module is the first runtime consumer of :class:`app.config.models.LlmSection`.
It loads the operator's ``llm-providers`` configuration (named by the
``LLM_CONFIG_PATH`` environment variable, surfaced via
:class:`app.settings.Settings`) **fail-safely**: a missing or invalid file logs
a credential-free warning and degrades to a deterministic-only posture with safe
default public-adviser limits -- the API never crashes because LLM config is
absent or malformed.

From a validated :class:`LlmSection` it builds the provider registry the router
uses. Providers are **disabled by default**: only ``enabled: true`` providers
with a recognised name (:data:`app.adviser.llm.adapters.REAL_ADAPTERS`) are
registered. In this slice every real provider ships ``enabled: false``, so the
registry is empty and the router always takes the deterministic path. The
loaded config and derived registry/limits are cached; :func:`reset_cache` clears
the cache for tests.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config.loader import load_and_validate
from app.config.models import LlmProvidersConfig, LlmSection, PublicAdviserLimits
from app.settings import get_settings

from .adapters import REAL_ADAPTERS
from .guards import ProviderBaseUrlError
from .protocol import ProviderTier
from .routing import RegisteredProvider

logger = logging.getLogger("freetier_atlas.adviser.llm")

#: Safe default public-adviser limits used when no LLM config is present. Mirror
#: the conservative example config; ``reject_urls`` and ``fallback_to_deterministic``
#: are on so the deterministic-only posture stays safe out of the box.
DEFAULT_LIMITS = PublicAdviserLimits(
    ai_requests_per_ip_per_day=0,
    deterministic_requests_per_ip_per_day=10,
    concurrent_requests_per_session=1,
    maximum_input_characters=2000,
    maximum_output_tokens=4000,
    require_captcha=False,
    reject_urls=True,
    allow_file_uploads=False,
    fallback_to_deterministic=True,
)

#: Provider name -> routing tier. Only these names are recognised.
_PROVIDER_TIERS: dict[str, ProviderTier] = {
    "ollama": ProviderTier.LOCAL,
    "gemini": ProviderTier.FREE_HOSTED,
    "openai": ProviderTier.COMMERCIAL,
    "anthropic": ProviderTier.COMMERCIAL,
}


@lru_cache(maxsize=1)
def get_llm_section() -> LlmSection | None:
    """Load and validate the LLM config, or ``None`` (deterministic-only).

    Fail-safe: any problem (unset path, missing file, malformed YAML, schema
    error, or a config that is not the ``llm-providers`` family) is logged
    without secret values and degrades to ``None``.
    """

    path = get_settings().llm_config_path
    if not path:
        return None
    try:
        config = load_and_validate(path)
    except Exception as exc:  # noqa: BLE001 - never let LLM config crash the API
        logger.warning(
            "LLM config at %r could not be loaded (%s); using deterministic-only adviser",
            path,
            type(exc).__name__,
        )
        return None
    if not isinstance(config, LlmProvidersConfig):
        logger.warning(
            "Config at %r is not an llm-providers file; using deterministic-only adviser",
            path,
        )
        return None
    return config.llm


def _consent_required(name: str, tier: ProviderTier, provider) -> bool:
    """External (non-local) tiers require consent; config may force it on."""

    if tier is not ProviderTier.LOCAL:
        return True
    return bool(getattr(provider, "external_processing_consent_required", False))


def build_registry(section: LlmSection | None) -> tuple[RegisteredProvider, ...]:
    """Build the enabled-provider registry from a validated ``LlmSection``.

    Disabled providers and unrecognised names are skipped (the latter logged).
    A provider whose configured ``base_url`` fails the egress guard is skipped
    rather than crashing the adviser.
    """

    if section is None:
        return ()
    registered: list[RegisteredProvider] = []
    for name, provider in section.providers.items():
        if not provider.enabled:
            continue
        tier = _PROVIDER_TIERS.get(name)
        adapter_cls = REAL_ADAPTERS.get(name)
        if tier is None or adapter_cls is None:
            logger.warning("Ignoring unknown enabled LLM provider %r", name)
            continue
        try:
            adapter = adapter_cls(model=provider.model)
        except ProviderBaseUrlError as exc:
            logger.warning("Skipping provider %r: %s", name, exc)
            continue
        registered.append(
            RegisteredProvider(
                name=name,
                tier=tier,
                provider=adapter,
                consent_required=_consent_required(name, tier, provider),
            )
        )
    return tuple(sorted(registered, key=lambda rp: rp.sort_key()))


@lru_cache(maxsize=1)
def get_registry() -> tuple[RegisteredProvider, ...]:
    """Return the cached enabled-provider registry (empty in this slice)."""

    return build_registry(get_llm_section())


def get_limits() -> PublicAdviserLimits:
    """Return the public-adviser limits from config, or the safe defaults."""

    section = get_llm_section()
    return section.public_adviser if section is not None else DEFAULT_LIMITS


def reset_cache() -> None:
    """Clear cached config/registry (used by tests after monkeypatching)."""

    get_llm_section.cache_clear()
    get_registry.cache_clear()


__all__ = [
    "DEFAULT_LIMITS",
    "get_llm_section",
    "build_registry",
    "get_registry",
    "get_limits",
    "reset_cache",
]
