"""Private GitHub-OAuth admin surface (F007 slice 4).

A small, dependency-free admin layer restricted to an allowlist of GitHub
logins. It authenticates operators through the standard GitHub OAuth web flow,
issues a *stateless* signed session cookie (stdlib HMAC -- no session table, no
JWT library, honouring owner decision Q6), and exposes four read/toggle admin
functions (Q5): the AI kill switch (wired to the existing S2 abuse mechanism),
the review/contradiction queue, a source-health view, and a validated YAML
config-diff view. Every authentication attempt and every mutating action is
appended to the ``admin_audit`` table (migration 0009).

The package adds no runtime dependency (Q9): FastAPI plus the Python standard
library only.
"""

from __future__ import annotations

from .router import router

__all__ = ["router"]
