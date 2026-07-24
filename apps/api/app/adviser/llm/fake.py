"""The deterministic fake interpreter -- the only LLM adapter exercised in CI.

``FakeInterpreter`` implements the :class:`~app.adviser.llm.protocol.LlmProvider`
seam with fully deterministic, offline behaviour so the routing ladder,
consent gating, and fail-closed schema validation can be tested without any
network, model, or real provider (owner decision Q1). It can be configured to:

* return a fixed candidate dict (well-formed *or* deliberately malformed, to
  prove the strict schema rejects bad LLM output and the router falls back);
* raise :class:`~app.adviser.llm.protocol.LlmTimeoutError` or
  :class:`~app.adviser.llm.protocol.LlmProviderError` (to prove timeout/error
  degrade to the next tier / deterministic fallback); or
* echo the description back through the deterministic parser (a harmless,
  offline "interpretation" for smoke-style tests).

It never touches the network, filesystem, shell, or any credential.
"""

from __future__ import annotations

from typing import Any

from .parser import deterministic_parse
from .protocol import LlmProviderError, LlmTimeoutError


class FakeInterpreter:
    """A deterministic, offline stand-in for a real LLM provider."""

    def __init__(
        self,
        *,
        candidate: dict[str, Any] | None = None,
        raise_timeout: bool = False,
        raise_error: bool = False,
        echo_parser: bool = False,
    ) -> None:
        self._candidate = candidate
        self._raise_timeout = raise_timeout
        self._raise_error = raise_error
        self._echo_parser = echo_parser
        #: Records that ``interpret`` was called, for consent-gating assertions.
        #: The description itself is never stored beyond the call.
        self.calls = 0

    def interpret(self, description: str, limits: Any = None) -> dict[str, object]:
        self.calls += 1
        if self._raise_timeout:
            raise LlmTimeoutError("fake interpreter timed out")
        if self._raise_error:
            raise LlmProviderError("fake interpreter failed")
        if self._echo_parser:
            parsed = deterministic_parse(description, limits)
            return parsed if parsed is not None else {"requirements": []}
        if self._candidate is not None:
            return self._candidate
        # No behaviour configured -> return an empty (schema-invalid) candidate so
        # the router falls back rather than trusting an unconfigured fake.
        return {"requirements": []}


__all__ = ["FakeInterpreter"]
