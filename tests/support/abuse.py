"""Shared test helpers for the abuse layer (F007 slice 2)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.adviser.abuse.config import AbuseConfig

FIXED_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)

_SECRET = "test-abuse-secret"  # pragma: allowlist secret


def make_config(
    *,
    enabled: bool = True,
    rate_window_seconds: int = 86_400,
    dedupe_window_seconds: int = 10,
    breaker_threshold: int = 3,
    breaker_cooldown_seconds: int = 30,
    pow_difficulty: int = 1,
    pow_ttl_seconds: int = 300,
    ai_kill_switch_override: bool = False,
) -> AbuseConfig:
    """Build a deterministic :class:`AbuseConfig` for tests."""

    return AbuseConfig(
        enabled=enabled,
        secret=_SECRET,
        rate_window_seconds=rate_window_seconds,
        dedupe_window_seconds=dedupe_window_seconds,
        breaker_threshold=breaker_threshold,
        breaker_cooldown_seconds=breaker_cooldown_seconds,
        pow_difficulty=pow_difficulty,
        pow_ttl_seconds=pow_ttl_seconds,
        ai_kill_switch_override=ai_kill_switch_override,
    )


def make_limits(
    *,
    deterministic_requests_per_ip_per_day: int = 10,
    ai_requests_per_ip_per_day: int = 0,
    require_captcha: bool = False,
) -> Any:
    """Return a minimal limits stand-in exposing the fields the service reads."""

    return SimpleNamespace(
        deterministic_requests_per_ip_per_day=deterministic_requests_per_ip_per_day,
        ai_requests_per_ip_per_day=ai_requests_per_ip_per_day,
        require_captcha=require_captcha,
    )


def make_request(ip: str = "203.0.113.5", forwarded: str | None = None) -> Any:
    """Return a lightweight request-like object with ``.headers`` and ``.client``."""

    headers: dict[str, str] = {}
    if forwarded is not None:
        headers["x-forwarded-for"] = forwarded
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=ip))
