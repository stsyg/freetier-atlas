"""Thin, config-gated adapters for the four real LLM providers.

These are **deliberately inert in slice 1**. F007 slice 1 ships the provider
abstraction, routing ladder, consent boundary, and deterministic fallback, but
performs **no** real LLM network I/O -- in CI or in the live smoke. Every real
provider stays ``enabled: false`` in configuration and, even if enabled, calling
:meth:`interpret` raises :class:`~app.adviser.llm.protocol.LlmProviderError` so
the router degrades gracefully to the deterministic path. The actual request
transport (with full per-request SSRF egress enforcement) arrives in a later
slice.

Each adapter records only the operator-supplied ``model`` and validates any
configured ``base_url`` through :func:`app.adviser.llm.guards.guard_base_url` at
construction. Adapters never receive or store a credential *value*: the config
carries only the *name* of an environment variable.
"""

from __future__ import annotations

from .guards import guard_base_url
from .protocol import LlmProviderError, ProviderTier

_NOT_IMPLEMENTED = (
    "real LLM providers perform no network I/O in this slice; "
    "the request degrades to the deterministic path"
)


class _RealAdapterBase:
    """Common construction + inert ``interpret`` for the real provider stubs."""

    #: Provider tier used by the registry when this adapter is enabled.
    tier: ProviderTier = ProviderTier.COMMERCIAL

    def __init__(self, *, model: str | None = None, base_url: str | None = None) -> None:
        self.model = model
        # base_url normally comes from the environment (named by *_base_url_env);
        # when a literal value is provided, guard its scheme/shape at construction.
        self.base_url = guard_base_url(base_url) if base_url else None

    def interpret(self, description: str, limits: object = None) -> dict[str, object]:
        raise LlmProviderError(_NOT_IMPLEMENTED)


class OllamaAdapter(_RealAdapterBase):
    """Local, self-hosted model (e.g. Ollama). Reached over the operator's own
    ``base_url``; needs no external-processing consent."""

    tier = ProviderTier.LOCAL


class GeminiAdapter(_RealAdapterBase):
    """Google Gemini free-hosted tier. External processing -> consent required."""

    tier = ProviderTier.FREE_HOSTED


class OpenAIAdapter(_RealAdapterBase):
    """OpenAI commercial escalation tier. External processing -> consent required."""

    tier = ProviderTier.COMMERCIAL


class AnthropicAdapter(_RealAdapterBase):
    """Anthropic commercial escalation tier. External processing -> consent required."""

    tier = ProviderTier.COMMERCIAL


#: The known real adapters, keyed by the provider name used in configuration.
#: Only these names are recognised when building the registry; an unknown name
#: is ignored (logged) rather than trusted.
REAL_ADAPTERS: dict[str, type[_RealAdapterBase]] = {
    "ollama": OllamaAdapter,
    "gemini": GeminiAdapter,
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
}


__all__ = [
    "OllamaAdapter",
    "GeminiAdapter",
    "OpenAIAdapter",
    "AnthropicAdapter",
    "REAL_ADAPTERS",
]
