"""LLM-assisted natural-language intake for the adviser (F007 slice 1).

This package adds a *strictly bounded* natural-language front door to the
deterministic adviser. Its entire job is to turn a free-text project
description into a **candidate structured requirements object** which is then
validated through the existing strict :class:`app.adviser.schema.RecommendationRequest`
(``extra="forbid"``, bounds, URL rejection, exact ``Decimal``) and handed to the
existing deterministic :func:`app.adviser.recommend.recommend`.

Design invariants (see ``docs/ARCHITECTURE.md`` "LLM routing" and
``docs/SECURITY_PRIVACY_ABUSE.md``):

* **The LLM never re-derives Z0 / quota / classification.** It only proposes a
  candidate request; every downstream decision stays in the deterministic core.
* **The LLM has one capability only** -- :meth:`LlmProvider.interpret`. It is
  given no credentials, filesystem, shell, URL-fetch, admin, or publication
  capability, and there is no direct LLM-to-publication path.
* **Deterministic fallback is always retained.** A deterministic rule-based
  parser is tried first; malformed / timed-out / errored / consent-absent LLM
  attempts degrade to the next tier and ultimately to a graceful deterministic
  response -- never a hard failure of the user's task.
* **Consent is ephemeral.** External providers require an explicit per-request
  consent assertion that is never persisted and never logged; the free-text
  description is never logged either.

The deterministic adviser core (``recommend`` / ``select`` / ``schema`` /
``schemas``) must never import this package; that no-LLM-import boundary is
asserted by a dedicated subprocess test.
"""

from __future__ import annotations

from .protocol import (
    LlmProvider,
    LlmProviderError,
    LlmTimeoutError,
    ProviderTier,
)
from .routing import RegisteredProvider, RoutingOutcome, route

__all__ = [
    "LlmProvider",
    "LlmProviderError",
    "LlmTimeoutError",
    "ProviderTier",
    "RegisteredProvider",
    "RoutingOutcome",
    "route",
]
