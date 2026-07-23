"""Read-only catalogue / provider HTTP API (F005 slice 3).

Exposes the *published* catalogue (produced by the S1 scan + S2 gated
publication path) over a small set of ``GET`` endpoints so the web experience
and API consumers can read it. Strictly read-only: no writes, no publication,
no LLM in the request path, and no user-controlled URLs.
"""

from __future__ import annotations

from .router import router

__all__ = ["router"]
