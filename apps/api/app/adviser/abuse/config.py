"""Immutable tuning knobs for the abuse layer, derived from settings.

:class:`AbuseConfig` is a frozen snapshot so enforcement is deterministic within
a request and trivially injectable in tests (no global lookups inside the
decision helpers). :func:`load_abuse_config` builds it from
:class:`app.settings.Settings`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.settings import Settings, get_settings


@dataclass(frozen=True)
class AbuseConfig:
    """A deterministic snapshot of the abuse-layer configuration."""

    enabled: bool
    secret: str
    rate_window_seconds: int
    dedupe_window_seconds: int
    breaker_threshold: int
    breaker_cooldown_seconds: int
    pow_difficulty: int
    pow_ttl_seconds: int
    ai_kill_switch_override: bool


def load_abuse_config(settings: Settings | None = None) -> AbuseConfig:
    """Build an :class:`AbuseConfig` from ``settings`` (defaults to the cached one)."""

    settings = settings or get_settings()
    return AbuseConfig(
        enabled=settings.abuse_enabled,
        secret=settings.abuse_secret,
        rate_window_seconds=settings.abuse_rate_window_seconds,
        dedupe_window_seconds=settings.abuse_dedupe_window_seconds,
        breaker_threshold=settings.abuse_breaker_threshold,
        breaker_cooldown_seconds=settings.abuse_breaker_cooldown_seconds,
        pow_difficulty=settings.abuse_pow_difficulty,
        pow_ttl_seconds=settings.abuse_pow_ttl_seconds,
        ai_kill_switch_override=settings.ai_kill_switch,
    )


__all__ = ["AbuseConfig", "load_abuse_config"]
