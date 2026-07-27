"""Unit tests for the abuse-layer keyed hashing / signing primitives."""

from __future__ import annotations

from app.adviser.abuse import hashing

_SECRET = "unit-secret"  # pragma: allowlist secret
_OTHER = "other-secret"  # pragma: allowlist secret


def test_hash_ip_is_deterministic_and_never_the_raw_ip() -> None:
    a = hashing.hash_ip(_SECRET, "203.0.113.7")
    b = hashing.hash_ip(_SECRET, "203.0.113.7")
    assert a == b
    assert "203.0.113.7" not in a
    assert len(a) == 64  # sha256 hex


def test_hash_ip_varies_by_secret_and_ip() -> None:
    assert hashing.hash_ip(_SECRET, "203.0.113.7") != hashing.hash_ip(_OTHER, "203.0.113.7")
    assert hashing.hash_ip(_SECRET, "203.0.113.7") != hashing.hash_ip(_SECRET, "203.0.113.8")


def test_hash_body_varies_by_scope_and_body() -> None:
    base = hashing.hash_body(_SECRET, "deterministic", '{"a":1}')
    assert base == hashing.hash_body(_SECRET, "deterministic", '{"a":1}')
    assert base != hashing.hash_body(_SECRET, "assisted", '{"a":1}')
    assert base != hashing.hash_body(_SECRET, "deterministic", '{"a":2}')


def test_sign_and_verify_round_trip_and_reject_tampering() -> None:
    sig = hashing.sign(_SECRET, "challenge.1.2.1")
    assert hashing.verify(_SECRET, "challenge.1.2.1", sig) is True
    assert hashing.verify(_SECRET, "challenge.1.2.2", sig) is False
    assert hashing.verify(_OTHER, "challenge.1.2.1", sig) is False
