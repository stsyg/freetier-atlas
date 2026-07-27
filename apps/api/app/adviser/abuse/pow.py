"""Self-hosted proof-of-work challenge (stdlib only; owner decision Q3).

Instead of an external CAPTCHA service, an expensive assisted request beyond the
free threshold must be accompanied by a solved proof-of-work challenge. The flow:

1. The client asks for a challenge (``POST /adviser/challenge``). The server
   mints a short, self-describing, **HMAC-signed** token and persists its
   issuance (for single-use + expiry enforcement).
2. The client finds a ``nonce`` such that
   ``sha256(f"{token}:{nonce}")`` has ``difficulty`` leading hex zeros, and
   submits ``token`` + ``nonce`` on its next assisted request.
3. The server verifies the signature (constant-time), the expiry, and the work,
   then atomically consumes the challenge so it cannot be replayed.

The difficulty is configurable and defaults low: it exists to make *automated*
abuse of the AI path costly, not to burden honest clients or tests. No secret is
ever placed in the token beyond the HMAC signature (which reveals nothing).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import hashing
from .config import AbuseConfig
from .store import AbuseStore

#: Opaque, versioned label describing how to solve a challenge (returned to the
#: client so the contract is self-documenting).
POW_ALGORITHM = "sha256-leading-hex-zeros-v1"

_TOKEN_PARTS = 5


@dataclass(frozen=True)
class ChallengeIssue:
    """A freshly minted, persisted proof-of-work challenge for the client."""

    challenge_id: str
    token: str
    difficulty: int
    algorithm: str
    expires_at: datetime


@dataclass(frozen=True)
class Verification:
    """Result of verifying a submitted ``token`` + ``nonce`` (before consume)."""

    ok: bool
    challenge_id: str | None
    reason: str | None


def _signing_payload(challenge_id: str, issued: int, expires: int, difficulty: int) -> str:
    return f"{challenge_id}.{issued}.{expires}.{difficulty}"


def issue_challenge(
    store: AbuseStore, config: AbuseConfig, ip_hash: str, now: datetime
) -> ChallengeIssue:
    """Mint, persist, and return a new proof-of-work challenge."""

    challenge_id = secrets.token_hex(16)
    issued = int(now.timestamp())
    expires_at = now + timedelta(seconds=config.pow_ttl_seconds)
    expires = int(expires_at.timestamp())
    difficulty = config.pow_difficulty
    payload = _signing_payload(challenge_id, issued, expires, difficulty)
    signature = hashing.sign(config.secret, payload)
    token = f"{payload}.{signature}"

    # Best-effort housekeeping so the issuance table stays small, then persist.
    store.pow_purge_expired(now)
    store.pow_issue(challenge_id, difficulty, ip_hash, now, expires_at)
    return ChallengeIssue(
        challenge_id=challenge_id,
        token=token,
        difficulty=difficulty,
        algorithm=POW_ALGORITHM,
        expires_at=expires_at,
    )


def _meets_difficulty(token: str, nonce: str, difficulty: int) -> bool:
    digest = hashlib.sha256(f"{token}:{nonce}".encode()).hexdigest()
    return digest.startswith("0" * difficulty)


def verify_solution(config: AbuseConfig, token: str, nonce: str, now: datetime) -> Verification:
    """Verify a submitted ``token`` + ``nonce`` (signature, expiry, work).

    This does not consume the challenge -- the caller consumes it via the store
    only once the request is actually admitted, so a valid proof is single-use.
    """

    parts = token.split(".")
    if len(parts) != _TOKEN_PARTS:
        return Verification(ok=False, challenge_id=None, reason="malformed_token")
    challenge_id, issued_s, expires_s, difficulty_s, signature = parts
    try:
        issued = int(issued_s)
        expires = int(expires_s)
        difficulty = int(difficulty_s)
    except ValueError:
        return Verification(ok=False, challenge_id=None, reason="malformed_token")

    payload = _signing_payload(challenge_id, issued, expires, difficulty)
    if not hashing.verify(config.secret, payload, signature):
        return Verification(ok=False, challenge_id=None, reason="bad_signature")
    if int(now.timestamp()) > expires:
        return Verification(ok=False, challenge_id=challenge_id, reason="expired")
    if not _meets_difficulty(token, nonce, difficulty):
        return Verification(ok=False, challenge_id=challenge_id, reason="insufficient_work")
    return Verification(ok=True, challenge_id=challenge_id, reason=None)


def solve(token: str, difficulty: int) -> str:
    """Find a nonce solving ``token`` at ``difficulty`` (helper for clients/tests)."""

    nonce = 0
    while True:
        candidate = str(nonce)
        if _meets_difficulty(token, candidate, difficulty):
            return candidate
        nonce += 1


__all__ = [
    "POW_ALGORITHM",
    "ChallengeIssue",
    "Verification",
    "issue_challenge",
    "verify_solution",
    "solve",
]
