"""Public-adviser abuse-control layer (F007 slice 2).

Enforces, for the public adviser endpoints, five stdlib-only protections that
persist their state in PostgreSQL (migration 0008) -- **no new runtime
dependency and no Redis** (owner decision Q9):

* **Per-IP rate limiting** -- fixed-window counters keyed by an HMAC of the
  client IP (the raw IP is never stored).
* **AI kill switch** -- a persisted flag that forces the assisted/LLM path to the
  deterministic fallback without hard-failing the request.
* **Per-provider circuit breaker** -- opens after N consecutive provider
  failures, routing skips the provider until a half-open probe succeeds.
* **Request dedupe** -- collapses byte-identical requests within a short window
  so a burst does not multiply LLM calls or burn the rate limit.
* **Self-hosted proof-of-work** -- a server-signed challenge (stdlib HMAC) the
  client must solve before an expensive assisted request beyond the free
  threshold is accepted (owner decision Q3: no external CAPTCHA).

The public surface used by the router is intentionally small: build an
:class:`AbuseConfig`, obtain the process-wide :class:`AbuseStore`, and call the
:mod:`~app.adviser.abuse.service` decision helpers.
"""

from __future__ import annotations

from .config import AbuseConfig, load_abuse_config
from .service import (
    AssistedDecision,
    RateDecision,
    client_ip_hash,
    enforce_deterministic,
    evaluate_assisted,
)
from .store import AbuseStore, InMemoryAbuseStore, get_abuse_store

__all__ = [
    "AbuseConfig",
    "load_abuse_config",
    "AbuseStore",
    "InMemoryAbuseStore",
    "get_abuse_store",
    "AssistedDecision",
    "RateDecision",
    "client_ip_hash",
    "enforce_deterministic",
    "evaluate_assisted",
]
