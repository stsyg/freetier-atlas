"""Typed, validated declarative configuration for FreeTier Atlas.

This package parses and validates the YAML configuration families under
``config/`` (see ``config/examples``). Configuration is declarative and typed;
secrets are referenced by environment-variable name only and are never stored
inline.
"""

from __future__ import annotations

from .loader import ConfigError, detect_family, load_and_validate
from .models import (
    FAMILY_MODELS,
    ApplicationConfig,
    LlmProvidersConfig,
    ProviderConfig,
    SchedulesConfig,
)

__all__ = [
    "ApplicationConfig",
    "ConfigError",
    "FAMILY_MODELS",
    "LlmProvidersConfig",
    "ProviderConfig",
    "SchedulesConfig",
    "detect_family",
    "load_and_validate",
]
