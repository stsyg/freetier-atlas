"""The narrow LLM provider seam and its tier ordering.

A provider's *only* capability is :meth:`LlmProvider.interpret`: given a
free-text description and the public-adviser limits, it proposes a **candidate
structured requirements dict** (the shape of
:class:`app.adviser.schema.RecommendationRequest`). It returns plain data only:
it never fetches URLs, touches the filesystem, runs a shell, receives
credentials, or publishes anything. The candidate is always validated through
the strict request schema before it is trusted, so a provider that returns
malformed or unsafe data simply causes a fail-closed fallback.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Protocol, runtime_checkable


class ProviderTier(IntEnum):
    """Routing tiers, ordered from cheapest/safest to most escalated.

    Lower values are attempted first. ``LOCAL`` runs on infrastructure the
    operator controls and needs no external-processing consent; ``FREE_HOSTED``
    and ``COMMERCIAL`` send the description to an external service and therefore
    require an explicit per-request consent assertion at the routing boundary.
    """

    LOCAL = 1
    FREE_HOSTED = 2
    COMMERCIAL = 3


class LlmError(Exception):
    """Base class for a recoverable LLM interpretation failure.

    Every subclass is caught at the routing boundary and degrades to the next
    tier (and ultimately to the deterministic fallback); an LLM failure never
    propagates as a hard failure of the user's request. ``reason`` is a stable,
    credential-free machine code suitable for a ``fallback_reason``.
    """

    reason = "llm_error"


class LlmTimeoutError(LlmError):
    """The provider did not answer within its configured deadline."""

    reason = "provider_timeout"


class LlmProviderError(LlmError):
    """The provider could not be reached or refused/failed the request.

    Also raised by the real adapters in this slice: they are thin, config-gated,
    and never perform network I/O here, so calling ``interpret`` raises this to
    force a graceful degrade to the deterministic path.
    """

    reason = "provider_error"


@runtime_checkable
class LlmProvider(Protocol):
    """The single-capability seam an adviser LLM adapter implements."""

    def interpret(self, description: str, limits: Any) -> dict[str, object]:
        """Propose a candidate structured requirements dict for ``description``.

        Must return plain JSON-serialisable data shaped like a
        :class:`app.adviser.schema.RecommendationRequest`. It must not raise for
        "I couldn't understand this" -- returning an empty/incomplete dict lets
        the strict schema reject it and the router fall back. It may raise
        :class:`LlmTimeoutError` / :class:`LlmProviderError` for transport-level
        failures, which the router treats as a degrade-to-next-tier signal.
        """
        ...  # pragma: no cover - protocol definition


__all__ = [
    "ProviderTier",
    "LlmError",
    "LlmTimeoutError",
    "LlmProviderError",
    "LlmProvider",
]
