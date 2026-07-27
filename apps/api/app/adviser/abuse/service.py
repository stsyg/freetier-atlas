"""Enforcement decisions for the abuse layer (F007 slice 2).

This module is the single place the router asks "may this request proceed, and
if not, how should it degrade?". It is deliberately free of FastAPI/HTTP types
(it takes a lightweight request-like object and the already-loaded limits) so it
is trivially unit-testable with an :class:`InMemoryAbuseStore` and an injected
``now``.

Two enforcement shapes, matching F007 acceptance step 1 vs step 4:

* :func:`enforce_deterministic` -- the deterministic endpoints
  (``/adviser/recommend``, ``/adviser/export``). A per-IP overage returns a hard
  **429** (the router adds ``Retry-After``); identical repeats inside the dedupe
  window are collapsed and do not burn the budget.
* :func:`evaluate_assisted` -- the assisted/LLM endpoint. It *never* hard-fails
  for an AI-quota reason: exhausting the AI budget, an open circuit, the kill
  switch, a required-but-missing proof-of-work, or a duplicate all cause a
  graceful **degrade to the deterministic fallback** (HTTP 200, ``llm_used``
  false, clear reason). The only 429 here is an absolute anti-hammering ceiling.

No raw IP or request body is stored -- only HMAC digests (see
:mod:`app.adviser.abuse.hashing`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import hashing, pow
from .config import AbuseConfig
from .store import AbuseStore

#: Rate-limit scopes (kept stable; they are part of persisted keys).
SCOPE_DETERMINISTIC = "deterministic"
SCOPE_ASSISTED = "assisted"
SCOPE_AI = "ai"

#: Persisted kill-switch flag name.
AI_KILL_SWITCH_FLAG = "ai_kill_switch"

#: Fallback reason codes surfaced when the assisted path degrades for an abuse
#: reason (distinct from the routing ladder's own reasons).
REASON_DEDUPLICATED = "deduplicated"
REASON_AI_KILL_SWITCH = "ai_kill_switch"
REASON_AI_QUOTA_EXHAUSTED = "ai_quota_exhausted"
REASON_POW_REQUIRED = "pow_required"


def _client_ip(request: Any) -> str:
    """Best-effort client IP: leftmost ``X-Forwarded-For`` else the socket peer.

    The raw value is used only transiently to compute an HMAC hash; it is never
    stored or logged.
    """

    headers = getattr(request, "headers", None)
    forwarded = headers.get("x-forwarded-for") if headers is not None else None
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return host or "unknown"


def client_ip_hash(config: AbuseConfig, request: Any) -> str:
    """Return the keyed HMAC hash of the request's client IP."""

    return hashing.hash_ip(config.secret, _client_ip(request))


def _canonical_body(body: Any) -> str:
    """Return a stable canonical JSON string for a pydantic request model."""

    if hasattr(body, "model_dump"):
        payload = body.model_dump(mode="json")
    else:  # pragma: no cover - defensive; router always passes a model
        payload = body
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _window_key(now: datetime, window_seconds: int) -> int:
    return int(now.timestamp() // window_seconds)


def _retry_after(now: datetime, window_key: int, window_seconds: int) -> int:
    remaining = int((window_key + 1) * window_seconds - now.timestamp())
    return max(1, remaining)


@dataclass(frozen=True)
class RateDecision:
    """Outcome of a deterministic-endpoint rate-limit check."""

    allowed: bool
    count: int
    limit: int
    retry_after: int
    deduplicated: bool


@dataclass(frozen=True)
class AssistedDecision:
    """Outcome of the assisted-endpoint gate.

    ``allow_ai`` -- run the LLM routing ladder (with the breaker-wrapped
    registry). When ``False`` the router runs a deterministic-only route and, if
    ``forced_reason`` is set and nothing was interpreted, surfaces that reason.
    ``rate_limited`` -- the only assisted hard-fail (absolute ceiling) -> 429.
    """

    allow_ai: bool
    degrade: bool
    forced_reason: str | None
    rate_limited: bool
    retry_after: int
    pow_required: bool
    deduplicated: bool
    pow_ok: bool


def enforce_deterministic(
    store: AbuseStore,
    config: AbuseConfig,
    request: Any,
    body: Any,
    scope: str,
    limit: int,
    now: datetime,
) -> RateDecision:
    """Per-IP rate-limit a deterministic request; collapse identical repeats."""

    if not config.enabled:
        return RateDecision(allowed=True, count=0, limit=limit, retry_after=0, deduplicated=False)

    ip_hash = client_ip_hash(config, request)
    dedupe_key = hashing.hash_body(config.secret, scope, _canonical_body(body))
    dedupe = store.record_dedupe(dedupe_key, scope, now, config.dedupe_window_seconds)
    if dedupe.is_duplicate:
        return RateDecision(allowed=True, count=0, limit=limit, retry_after=0, deduplicated=True)

    window_key = _window_key(now, config.rate_window_seconds)
    count = store.incr_rate(ip_hash, scope, window_key, now)
    if count > limit:
        return RateDecision(
            allowed=False,
            count=count,
            limit=limit,
            retry_after=_retry_after(now, window_key, config.rate_window_seconds),
            deduplicated=False,
        )
    return RateDecision(allowed=True, count=count, limit=limit, retry_after=0, deduplicated=False)


def evaluate_assisted(
    store: AbuseStore,
    config: AbuseConfig,
    limits: Any,
    request: Any,
    body: Any,
    *,
    had_providers: bool,
    pow_token: str | None,
    pow_nonce: str | None,
    now: datetime,
) -> AssistedDecision:
    """Decide whether the assisted request may use the LLM path, or must degrade."""

    if not config.enabled:
        return AssistedDecision(
            allow_ai=had_providers,
            degrade=False,
            forced_reason=None,
            rate_limited=False,
            retry_after=0,
            pow_required=False,
            deduplicated=False,
            pow_ok=False,
        )

    ip_hash = client_ip_hash(config, request)
    dedupe_key = hashing.hash_body(config.secret, SCOPE_ASSISTED, _canonical_body(body))
    dedupe = store.record_dedupe(dedupe_key, SCOPE_ASSISTED, now, config.dedupe_window_seconds)
    if dedupe.is_duplicate:
        # Collapse the duplicate: do not burn counters and do not call the LLM.
        return AssistedDecision(
            allow_ai=False,
            degrade=True,
            forced_reason=REASON_DEDUPLICATED,
            rate_limited=False,
            retry_after=0,
            pow_required=False,
            deduplicated=True,
            pow_ok=False,
        )

    # Absolute anti-hammering ceiling (the only assisted 429). Reuses the
    # deterministic per-IP/day budget as a generous upper bound.
    ceiling = int(getattr(limits, "deterministic_requests_per_ip_per_day", 0))
    window_key = _window_key(now, config.rate_window_seconds)
    ceiling_count = store.incr_rate(ip_hash, SCOPE_ASSISTED, window_key, now)
    if ceiling_count > ceiling:
        return AssistedDecision(
            allow_ai=False,
            degrade=False,
            forced_reason=None,
            rate_limited=True,
            retry_after=_retry_after(now, window_key, config.rate_window_seconds),
            pow_required=False,
            deduplicated=False,
            pow_ok=False,
        )

    # With no enabled provider there is no AI path to gate: the request is
    # deterministic-only regardless, so let the routing ladder speak for itself.
    if not had_providers:
        return AssistedDecision(
            allow_ai=False,
            degrade=False,
            forced_reason=None,
            rate_limited=False,
            retry_after=0,
            pow_required=False,
            deduplicated=False,
            pow_ok=False,
        )

    # AI kill switch (persisted flag OR env override) forces deterministic.
    if config.ai_kill_switch_override or store.get_flag(AI_KILL_SWITCH_FLAG):
        return AssistedDecision(
            allow_ai=False,
            degrade=True,
            forced_reason=REASON_AI_KILL_SWITCH,
            rate_limited=False,
            retry_after=0,
            pow_required=False,
            deduplicated=False,
            pow_ok=False,
        )

    # AI per-IP/day free threshold.
    free = int(getattr(limits, "ai_requests_per_ip_per_day", 0))
    ai_count = store.incr_rate(ip_hash, SCOPE_AI, window_key, now)
    if ai_count <= free:
        return AssistedDecision(
            allow_ai=True,
            degrade=False,
            forced_reason=None,
            rate_limited=False,
            retry_after=0,
            pow_required=False,
            deduplicated=False,
            pow_ok=False,
        )

    # Beyond the free threshold: a solved proof-of-work extends the budget.
    if pow_token and pow_nonce:
        verification = pow.verify_solution(config, pow_token, pow_nonce, now)
        if verification.ok and verification.challenge_id is not None:
            if store.pow_consume(verification.challenge_id, ip_hash, now):
                return AssistedDecision(
                    allow_ai=True,
                    degrade=False,
                    forced_reason=None,
                    rate_limited=False,
                    retry_after=0,
                    pow_required=False,
                    deduplicated=False,
                    pow_ok=True,
                )

    if bool(getattr(limits, "require_captcha", False)):
        return AssistedDecision(
            allow_ai=False,
            degrade=True,
            forced_reason=REASON_POW_REQUIRED,
            rate_limited=False,
            retry_after=0,
            pow_required=True,
            deduplicated=False,
            pow_ok=False,
        )
    return AssistedDecision(
        allow_ai=False,
        degrade=True,
        forced_reason=REASON_AI_QUOTA_EXHAUSTED,
        rate_limited=False,
        retry_after=0,
        pow_required=False,
        deduplicated=False,
        pow_ok=False,
    )


__all__ = [
    "SCOPE_DETERMINISTIC",
    "SCOPE_ASSISTED",
    "SCOPE_AI",
    "AI_KILL_SWITCH_FLAG",
    "REASON_DEDUPLICATED",
    "REASON_AI_KILL_SWITCH",
    "REASON_AI_QUOTA_EXHAUSTED",
    "REASON_POW_REQUIRED",
    "RateDecision",
    "AssistedDecision",
    "client_ip_hash",
    "enforce_deterministic",
    "evaluate_assisted",
]
