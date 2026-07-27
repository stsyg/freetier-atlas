"""Per-provider circuit breaker (F007 slice 2).

After ``breaker_threshold`` consecutive failures/timeouts from a provider, its
breaker **opens** for ``breaker_cooldown_seconds``; while open the provider is
skipped without being called. After the cooldown a single **half-open** probe is
allowed: success closes the breaker, failure re-opens it with a fresh cooldown.

Integration is deliberately transparent to the routing ladder. Each registered
provider is wrapped in a :class:`BreakerProvider` that implements the same
``interpret`` seam. When the breaker is open it raises :class:`CircuitOpenError`
(an :class:`LlmProviderError` with ``reason='circuit_open'``); the existing
:func:`app.adviser.llm.routing.route` already catches any :class:`LlmError`, uses
its ``reason``, and degrades to the next tier / deterministic fallback. So no
change to ``route`` is required.

Breaker state is persisted in PostgreSQL so it survives an API restart (a
provider that is down stays "known down" across restarts within the cooldown),
without adding Redis.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from ..llm.protocol import LlmError, LlmProvider, LlmProviderError
from ..llm.routing import RegisteredProvider
from .config import AbuseConfig
from .store import AbuseStore, BreakerRecord

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"

_CLOSED = BreakerRecord(state=STATE_CLOSED, consecutive_failures=0, opened_at=None)


class CircuitOpenError(LlmProviderError):
    """Raised when a provider's breaker is open, so the router skips it."""

    reason = "circuit_open"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class BreakerProvider:
    """Wrap a provider with persisted circuit-breaker admission control."""

    def __init__(
        self,
        name: str,
        inner: LlmProvider,
        store: AbuseStore,
        config: AbuseConfig,
        now_fn: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._name = name
        self._inner = inner
        self._store = store
        self._config = config
        self._now_fn = now_fn

    def interpret(self, description: str, limits: Any) -> dict[str, object]:
        now = self._now_fn()
        record = self._store.breaker_load(self._name) or _CLOSED

        if record.state == STATE_OPEN:
            elapsed = (
                (now - record.opened_at).total_seconds() if record.opened_at is not None else None
            )
            if elapsed is None or elapsed < self._config.breaker_cooldown_seconds:
                raise CircuitOpenError("circuit open")
            # Cooldown elapsed -> allow a single half-open probe (this call).

        try:
            result = self._inner.interpret(description, limits)
        except LlmError:
            self._record_failure(record, now)
            raise
        self._store.breaker_store(self._name, _CLOSED, now)
        return result

    def _record_failure(self, record: BreakerRecord, now: datetime) -> None:
        failures = record.consecutive_failures + 1
        if failures >= self._config.breaker_threshold or record.state != STATE_CLOSED:
            new = BreakerRecord(state=STATE_OPEN, consecutive_failures=failures, opened_at=now)
        else:
            new = BreakerRecord(state=STATE_CLOSED, consecutive_failures=failures, opened_at=None)
        self._store.breaker_store(self._name, new, now)


def wrap_registry(
    registry: Sequence[RegisteredProvider],
    store: AbuseStore,
    config: AbuseConfig,
    now_fn: Callable[[], datetime] = _utcnow,
) -> tuple[RegisteredProvider, ...]:
    """Return ``registry`` with every provider wrapped in a :class:`BreakerProvider`."""

    return tuple(
        RegisteredProvider(
            name=rp.name,
            tier=rp.tier,
            provider=BreakerProvider(rp.name, rp.provider, store, config, now_fn),
            consent_required=rp.consent_required,
        )
        for rp in registry
    )


__all__ = [
    "STATE_CLOSED",
    "STATE_OPEN",
    "STATE_HALF_OPEN",
    "CircuitOpenError",
    "BreakerProvider",
    "wrap_registry",
]
