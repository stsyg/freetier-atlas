"""Unit tests for the abuse admin helpers (kill switch / breaker reset)."""

from __future__ import annotations

from app.adviser.abuse import admin
from app.adviser.abuse.service import AI_KILL_SWITCH_FLAG
from app.adviser.abuse.store import BreakerRecord, InMemoryAbuseStore

from tests.support.abuse import FIXED_NOW


def test_set_kill_switch_toggles_persisted_flag() -> None:
    store = InMemoryAbuseStore()
    admin.set_kill_switch(store, True, FIXED_NOW)
    assert store.get_flag(AI_KILL_SWITCH_FLAG) is True
    admin.set_kill_switch(store, False, FIXED_NOW)
    assert store.get_flag(AI_KILL_SWITCH_FLAG) is False


def test_reset_breaker_forces_closed_state() -> None:
    store = InMemoryAbuseStore()
    store.breaker_store(
        "ollama",
        BreakerRecord(state="open", consecutive_failures=5, opened_at=FIXED_NOW),
        FIXED_NOW,
    )
    admin.reset_breaker(store, "ollama", FIXED_NOW)
    record = store.breaker_load("ollama")
    assert record.state == "closed"
    assert record.consecutive_failures == 0
    assert record.opened_at is None


def test_main_kill_switch_on_and_status(monkeypatch, capsys) -> None:
    store = InMemoryAbuseStore()
    monkeypatch.setattr("app.adviser.abuse.admin.get_abuse_store", lambda: store)

    assert admin.main(["kill-switch", "on"]) == 0
    assert "ai_kill_switch=on" in capsys.readouterr().out
    assert store.get_flag(AI_KILL_SWITCH_FLAG) is True

    assert admin.main(["status"]) == 0
    assert "ai_kill_switch=on" in capsys.readouterr().out
