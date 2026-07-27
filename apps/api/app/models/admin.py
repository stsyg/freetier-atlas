"""Admin audit-log persistence (F007 slice 4).

A single append-only table backs the private GitHub-OAuth admin surface. Every
admin authentication attempt (success *and* denial) and every admin action
(kill-switch toggle, review-queue disposition, source-health / config-diff view)
is appended here so the operator has a tamper-evident trail.

Privacy / security posture (``docs/SECURITY_PRIVACY_ABUSE.md``): an audit row
**never** stores a secret -- no OAuth client secret, no access token, no cookie
signing key, and no raw ``code``/``state`` value. It records only the actor's
public GitHub login (or ``None`` when identity was never established, e.g. a
forged cookie), a stable ``action`` code, an ``outcome`` (``success`` /
``denied``), an optional machine-readable ``reason`` for a denial, and a small
non-secret ``context`` mapping. The table is append-only in spirit: the
application never exposes an update or delete path for it.

It is an ordinary domain-owned table (it lives on :data:`app.models.metadata`)
so Alembic migration ``0009`` and this ORM model are drift-checked together.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

#: Stable ``outcome`` values (enforced in application code, not by a DB CHECK, so
#: the ORM metadata and the migration compare cleanly).
AUDIT_OUTCOMES: tuple[str, ...] = ("success", "denied")


class AdminAudit(Base):
    """One append-only admin audit-trail row.

    ``actor`` is the authenticated GitHub login when known (``None`` when the
    attempt never established identity). ``action`` is a stable code such as
    ``login`` / ``logout`` / ``kill_switch_toggle`` / ``review_action`` /
    ``source_health_view`` / ``config_diff``. ``outcome`` is ``success`` or
    ``denied``; ``reason`` is a short machine code for a denial (e.g.
    ``not_allowlisted``, ``invalid_state``, ``invalid_cookie``, ``invalid_csrf``,
    ``unauthenticated``). ``context`` is a small, non-secret JSON mapping.
    """

    __tablename__ = "admin_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["AdminAudit", "AUDIT_OUTCOMES"]
