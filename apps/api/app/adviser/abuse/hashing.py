"""Keyed hashing / signing primitives for the abuse layer (stdlib only).

Everything here is built on :mod:`hmac` / :mod:`hashlib` -- no third-party
crypto or token library (owner decision Q6). Two guarantees this module exists
to uphold:

* A **raw client IP is never stored or logged.** :func:`hash_ip` turns an IP
  into an opaque, keyed HMAC-SHA256 digest; only that digest is persisted.
* A **request body is never stored.** :func:`hash_body` turns a normalised body
  into a keyed digest used purely as a dedupe/idempotency key.

The same server secret keys the proof-of-work challenge signatures
(:func:`sign` / :func:`verify`), so a client cannot forge a challenge.
"""

from __future__ import annotations

import hashlib
import hmac


def _digest(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_ip(secret: str, ip: str) -> str:
    """Return a keyed, opaque digest of ``ip`` (the raw IP is never stored)."""

    return _digest(secret, f"ip:{ip}")


def hash_body(secret: str, scope: str, body: str) -> str:
    """Return a keyed dedupe key for a normalised request ``body`` in ``scope``."""

    return _digest(secret, f"body:{scope}:{body}")


def sign(secret: str, message: str) -> str:
    """Return the HMAC-SHA256 signature of ``message`` (hex)."""

    return _digest(secret, message)


def verify(secret: str, message: str, signature: str) -> bool:
    """Constant-time check that ``signature`` matches ``message``."""

    return hmac.compare_digest(sign(secret, message), signature)


__all__ = ["hash_ip", "hash_body", "sign", "verify"]
