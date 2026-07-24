"""The adviser routing ladder: deterministic parser -> LLM tiers -> fallback.

``route`` implements the ordered ladder from ``docs/ARCHITECTURE.md`` "LLM
routing":

1. **Deterministic parser** (:func:`app.adviser.llm.parser.deterministic_parse`)
   -- rule-based, no LLM, no consent. If it yields a schema-valid candidate the
   ladder stops here (``llm_used=False``).
2. **Local model** (no external-processing consent required).
3. **Free hosted model** (external -> consent required).
4. **Commercial model** (external -> consent required).
5. **Deterministic fallback** -- a graceful "couldn't interpret" outcome
   (``interpretation=None``) with a clear ``fallback_reason``.

Every candidate an LLM proposes is validated through the existing strict
:class:`app.adviser.schema.RecommendationRequest` before it is trusted; a
timeout, provider error, absent consent, or schema-rejected candidate simply
degrades to the next tier and ultimately to the deterministic fallback. The
free-text description is passed to providers but is never logged here.

Invariant: ``llm_used`` is ``True`` **iff** an LLM produced the trusted
interpretation; whenever ``llm_used`` is ``False`` a ``fallback_reason`` is set
explaining why the deterministic path was used (including the honest
``"deterministic_parser"`` when tier 1 sufficed).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..schema import RecommendationRequest
from .parser import deterministic_parse
from .protocol import LlmError, LlmProvider, ProviderTier

#: Reason codes surfaced in ``RoutingOutcome.fallback_reason``. Stable and
#: credential-free (safe to return to clients and record in metrics).
REASON_DETERMINISTIC_PARSER = "deterministic_parser"
REASON_NOT_INTERPRETABLE = "not_interpretable"
REASON_NO_PROVIDER_ENABLED = "no_provider_enabled"
REASON_CONSENT_NOT_GRANTED = "consent_not_granted"
REASON_INVALID_INTERPRETATION = "invalid_interpretation"


@dataclass(frozen=True)
class RegisteredProvider:
    """One enabled provider in the registry, with its tier and consent policy."""

    name: str
    tier: ProviderTier
    provider: LlmProvider
    consent_required: bool

    def sort_key(self) -> tuple[int, str]:
        return (int(self.tier), self.name)


@dataclass(frozen=True)
class RoutingOutcome:
    """The result of running the ladder for one request.

    * ``interpretation`` -- the trusted, schema-valid structured request, or
      ``None`` when nothing could be interpreted (deterministic fallback).
    * ``llm_used`` -- ``True`` iff an LLM produced ``interpretation``.
    * ``provider`` / ``tier`` -- the source of the interpretation
      (``"deterministic_parser"`` / ``"deterministic_fallback"`` for the
      non-LLM tiers).
    * ``routing_path`` -- the ordered tiers attempted.
    * ``fallback_reason`` -- why the deterministic path was used (``None`` only
      when ``llm_used`` is ``True``).
    * ``external_processing_used`` -- ``True`` iff an external (non-local)
      provider actually processed the description.
    """

    interpretation: RecommendationRequest | None
    llm_used: bool
    provider: str | None
    tier: str
    routing_path: tuple[str, ...]
    fallback_reason: str | None
    external_processing_used: bool = False

    @property
    def interpreted(self) -> bool:
        return self.interpretation is not None


def _validate(candidate: Any) -> RecommendationRequest | None:
    """Return a validated request, or ``None`` if the candidate is rejected."""

    if not isinstance(candidate, dict):
        return None
    try:
        return RecommendationRequest.model_validate(candidate)
    except ValidationError:
        return None


def route(
    description: str,
    limits: Any,
    registry: Sequence[RegisteredProvider],
    *,
    external_processing_consented: bool = False,
    parser: Callable[[str, Any], dict[str, Any] | None] = deterministic_parse,
) -> RoutingOutcome:
    """Run the routing ladder for ``description`` and return the outcome."""

    path: list[str] = []

    # Tier 1: deterministic parser (no LLM, no consent).
    path.append("deterministic_parser")
    parsed = parser(description, limits)
    if parsed is not None:
        request = _validate(parsed)
        if request is not None:
            return RoutingOutcome(
                interpretation=request,
                llm_used=False,
                provider=None,
                tier="deterministic_parser",
                routing_path=tuple(path),
                fallback_reason=REASON_DETERMINISTIC_PARSER,
                external_processing_used=False,
            )

    # Tiers 2-4: enabled LLM providers, ordered by tier then name.
    last_reason: str | None = None
    ordered = sorted(registry, key=lambda rp: rp.sort_key())
    for rp in ordered:
        label = f"{rp.tier.name.lower()}:{rp.name}"
        path.append(label)
        if rp.consent_required and not external_processing_consented:
            last_reason = REASON_CONSENT_NOT_GRANTED
            continue
        try:
            candidate = rp.provider.interpret(description, limits)
        except LlmError as exc:
            last_reason = exc.reason
            continue
        request = _validate(candidate)
        if request is None:
            last_reason = REASON_INVALID_INTERPRETATION
            continue
        return RoutingOutcome(
            interpretation=request,
            llm_used=True,
            provider=rp.name,
            tier=rp.tier.name.lower(),
            routing_path=tuple(path),
            fallback_reason=None,
            external_processing_used=rp.tier != ProviderTier.LOCAL,
        )

    # Tier 5: deterministic fallback (graceful "couldn't interpret").
    path.append("deterministic_fallback")
    if last_reason is None:
        last_reason = REASON_NO_PROVIDER_ENABLED if not ordered else REASON_NOT_INTERPRETABLE
    return RoutingOutcome(
        interpretation=None,
        llm_used=False,
        provider=None,
        tier="deterministic_fallback",
        routing_path=tuple(path),
        fallback_reason=last_reason,
        external_processing_used=False,
    )


__all__ = [
    "RegisteredProvider",
    "RoutingOutcome",
    "route",
    "REASON_DETERMINISTIC_PARSER",
    "REASON_NOT_INTERPRETABLE",
    "REASON_NO_PROVIDER_ENABLED",
    "REASON_CONSENT_NOT_GRANTED",
    "REASON_INVALID_INTERPRETATION",
]
