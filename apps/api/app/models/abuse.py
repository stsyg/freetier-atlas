"""Public-adviser abuse-control persistence (F007 slice 2).

Five small operational tables back the per-IP rate limiting, request dedupe,
AI kill switch, per-provider circuit breaker, and self-hosted proof-of-work
challenge tracking that protect the public adviser endpoints. They are ordinary
domain-owned tables (they live on :data:`app.models.metadata`) so the Alembic
migration and these ORM models are drift-checked together.

Privacy posture (``docs/SECURITY_PRIVACY_ABUSE.md``): a **raw client IP is never
stored**. Only an HMAC-SHA256 hash of the IP (keyed by a server secret) is
persisted in ``rate_limit_bucket``/``pow_challenge``. Request *bodies* are never
stored either -- only an HMAC digest of the normalised body is kept for dedupe.
Consent assertions and free-text descriptions are never persisted or logged.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class RateLimitBucket(Base):
    """One fixed-window per-IP counter for a request scope.

    The natural composite key ``(ip_hash, scope, window_key)`` is the upsert
    conflict target. ``ip_hash`` is an HMAC of the client IP (never the raw IP);
    ``scope`` distinguishes e.g. the deterministic endpoints from the assisted
    AI path; ``window_key`` is ``floor(epoch / window_seconds)``.
    """

    __tablename__ = "rate_limit_bucket"

    ip_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    window_key: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = _updated_at()


class AbuseFlag(Base):
    """A persisted boolean control flag (e.g. the AI kill switch).

    A single-row-per-flag key/value shape so an admin surface (F007 slice 4)
    can toggle controls without a schema change. ``name='ai_kill_switch'`` with
    ``enabled=true`` forces the assisted endpoint's LLM path to the deterministic
    fallback without hard-failing the request.
    """

    __tablename__ = "abuse_flag"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = _updated_at()


class CircuitBreaker(Base):
    """Per-provider circuit-breaker state, persisted so it survives restarts.

    ``state`` is one of ``closed`` / ``open`` / ``half_open`` (enforced in the
    application, not by a DB CHECK, to keep the ORM/migration comparison stable).
    After ``opened_at`` + the configured cooldown the breaker admits a single
    half-open probe; a success closes it, a failure re-opens it.
    """

    __tablename__ = "circuit_breaker"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = _updated_at()


class RequestDedupe(Base):
    """Dedupe/idempotency record collapsing identical requests within a window.

    ``dedupe_key`` is an HMAC of ``scope + normalised-request-body`` (never the
    body itself). A second identical request whose ``last_seen`` is still inside
    the configured window is treated as a duplicate and collapsed so it neither
    multiplies LLM calls nor burns the rate limit.
    """

    __tablename__ = "request_dedupe"

    dedupe_key: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False)


class PowChallenge(Base):
    """A single-use, server-signed proof-of-work challenge issuance.

    The token itself is HMAC-signed and self-describing, but the issuance is
    tracked here to enforce single use (``solved_at``) and expiry. ``ip_hash`` is
    an HMAC of the requesting IP (never the raw IP).
    """

    __tablename__ = "pow_challenge"

    challenge_id: Mapped[str] = mapped_column(Text, primary_key=True)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    ip_hash: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    solved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "RateLimitBucket",
    "AbuseFlag",
    "CircuitBreaker",
    "RequestDedupe",
    "PowChallenge",
]
