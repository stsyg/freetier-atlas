"""Unit tests for :class:`InMemoryAbuseStore` semantics (mirrors Postgres)."""

from __future__ import annotations

from datetime import timedelta

from app.adviser.abuse.store import BreakerRecord, InMemoryAbuseStore

from tests.support.abuse import FIXED_NOW


def test_incr_rate_accumulates_per_key() -> None:
    store = InMemoryAbuseStore()
    assert store.incr_rate("ip", "deterministic", 1, FIXED_NOW) == 1
    assert store.incr_rate("ip", "deterministic", 1, FIXED_NOW) == 2
    # Different window / scope / ip are independent counters.
    assert store.incr_rate("ip", "deterministic", 2, FIXED_NOW) == 1
    assert store.incr_rate("ip", "ai", 1, FIXED_NOW) == 1
    assert store.incr_rate("ip2", "deterministic", 1, FIXED_NOW) == 1


def test_record_dedupe_collapses_within_window_and_resets_after() -> None:
    store = InMemoryAbuseStore()
    first = store.record_dedupe("k", "assisted", FIXED_NOW, 10)
    assert first.is_duplicate is False and first.hit_count == 1

    dup = store.record_dedupe("k", "assisted", FIXED_NOW + timedelta(seconds=5), 10)
    assert dup.is_duplicate is True and dup.hit_count == 2

    # Beyond the window the key is treated as fresh again.
    fresh = store.record_dedupe("k", "assisted", FIXED_NOW + timedelta(seconds=30), 10)
    assert fresh.is_duplicate is False and fresh.hit_count == 1


def test_flags_default_false_and_persist() -> None:
    store = InMemoryAbuseStore()
    assert store.get_flag("ai_kill_switch") is False
    store.set_flag("ai_kill_switch", True, FIXED_NOW)
    assert store.get_flag("ai_kill_switch") is True
    store.set_flag("ai_kill_switch", False, FIXED_NOW)
    assert store.get_flag("ai_kill_switch") is False


def test_breaker_round_trips() -> None:
    store = InMemoryAbuseStore()
    assert store.breaker_load("ollama") is None
    record = BreakerRecord(state="open", consecutive_failures=3, opened_at=FIXED_NOW)
    store.breaker_store("ollama", record, FIXED_NOW)
    loaded = store.breaker_load("ollama")
    assert loaded == record


def test_pow_consume_is_single_use_and_respects_expiry_and_ip() -> None:
    store = InMemoryAbuseStore()
    expires = FIXED_NOW + timedelta(seconds=300)
    store.pow_issue("cid", 1, "iphash", FIXED_NOW, expires)

    # Wrong IP is rejected.
    assert store.pow_consume("cid", "other", FIXED_NOW) is False
    # Correct IP consumes once...
    assert store.pow_consume("cid", "iphash", FIXED_NOW) is True
    # ...and cannot be replayed.
    assert store.pow_consume("cid", "iphash", FIXED_NOW) is False

    # Expired challenge cannot be consumed.
    store.pow_issue("cid2", 1, "iphash", FIXED_NOW, expires)
    assert store.pow_consume("cid2", "iphash", expires + timedelta(seconds=1)) is False


def test_pow_purge_expired_removes_only_unsolved_expired() -> None:
    store = InMemoryAbuseStore()
    expires = FIXED_NOW + timedelta(seconds=1)
    store.pow_issue("old", 1, "iphash", FIXED_NOW, expires)
    store.pow_purge_expired(FIXED_NOW + timedelta(seconds=5))
    # Purged -> can no longer be consumed even with the right IP/time.
    assert store.pow_consume("old", "iphash", FIXED_NOW) is False
