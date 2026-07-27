"""Unit tests for the self-hosted proof-of-work challenge."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from app.adviser.abuse import pow as powmod
from app.adviser.abuse.store import InMemoryAbuseStore

from tests.support.abuse import FIXED_NOW, make_config


def test_issue_persists_and_token_is_solvable_and_verifiable() -> None:
    store = InMemoryAbuseStore()
    config = make_config(pow_difficulty=1)
    issued = powmod.issue_challenge(store, config, "iphash", FIXED_NOW)

    assert issued.difficulty == 1
    nonce = powmod.solve(issued.token, issued.difficulty)
    digest = hashlib.sha256(f"{issued.token}:{nonce}".encode()).hexdigest()
    assert digest.startswith("0")

    verification = powmod.verify_solution(config, issued.token, nonce, FIXED_NOW)
    assert verification.ok is True
    assert verification.challenge_id == issued.challenge_id
    # The challenge was persisted and is single-use.
    assert store.pow_consume(issued.challenge_id, "iphash", FIXED_NOW) is True
    assert store.pow_consume(issued.challenge_id, "iphash", FIXED_NOW) is False


def test_verify_rejects_tampered_signature() -> None:
    config = make_config()
    store = InMemoryAbuseStore()
    issued = powmod.issue_challenge(store, config, "iphash", FIXED_NOW)
    nonce = powmod.solve(issued.token, issued.difficulty)
    tampered = issued.token[:-1] + ("0" if issued.token[-1] != "0" else "1")
    assert powmod.verify_solution(config, tampered, nonce, FIXED_NOW).ok is False


def test_verify_rejects_expired_and_insufficient_work() -> None:
    config = make_config(pow_difficulty=2, pow_ttl_seconds=60)
    store = InMemoryAbuseStore()
    issued = powmod.issue_challenge(store, config, "iphash", FIXED_NOW)

    # Expired.
    good_nonce = powmod.solve(issued.token, issued.difficulty)
    later = FIXED_NOW + timedelta(seconds=120)
    assert powmod.verify_solution(config, issued.token, good_nonce, later).reason == "expired"

    # Insufficient work: deterministically pick a nonce that does NOT satisfy
    # the difficulty, and confirm verification rejects it as such.
    failing_nonce = next(
        str(n)
        for n in range(10_000)
        if not hashlib.sha256(f"{issued.token}:{n}".encode()).hexdigest().startswith("00")
    )
    bad = powmod.verify_solution(config, issued.token, failing_nonce, FIXED_NOW)
    assert bad.ok is False and bad.reason == "insufficient_work"


def test_verify_rejects_malformed_token() -> None:
    config = make_config()
    assert powmod.verify_solution(config, "not-a-valid-token", "0", FIXED_NOW).ok is False
