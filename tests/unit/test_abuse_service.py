"""Unit tests for the abuse enforcement decisions (service layer)."""

from __future__ import annotations

from app.adviser.abuse import pow as powmod
from app.adviser.abuse import service
from app.adviser.abuse.service import (
    AI_KILL_SWITCH_FLAG,
    REASON_AI_KILL_SWITCH,
    REASON_AI_QUOTA_EXHAUSTED,
    REASON_DEDUPLICATED,
    REASON_POW_REQUIRED,
    SCOPE_DETERMINISTIC,
)
from app.adviser.abuse.store import InMemoryAbuseStore

from tests.support.abuse import FIXED_NOW, make_config, make_limits, make_request


def _call_det(store, config, body, limit=10):
    return service.enforce_deterministic(
        store, config, make_request(), body, SCOPE_DETERMINISTIC, limit, FIXED_NOW
    )


def _call_assisted(store, config, limits, body, **kw):
    kw.setdefault("had_providers", True)
    kw.setdefault("pow_token", None)
    kw.setdefault("pow_nonce", None)
    return service.evaluate_assisted(
        store, config, limits, make_request(), body, now=FIXED_NOW, **kw
    )


# --- enforce_deterministic --------------------------------------------------


def test_deterministic_disabled_always_allows() -> None:
    decision = _call_det(InMemoryAbuseStore(), make_config(enabled=False), {"n": 1})
    assert decision.allowed is True


def test_deterministic_overage_returns_429_with_retry_after() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    assert _call_det(store, config, {"n": 1}, limit=2).allowed is True
    assert _call_det(store, config, {"n": 2}, limit=2).allowed is True
    over = _call_det(store, config, {"n": 3}, limit=2)
    assert over.allowed is False
    assert over.retry_after > 0


def test_deterministic_duplicate_is_collapsed_and_does_not_burn_budget() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    first = _call_det(store, config, {"n": 1}, limit=1)
    assert first.allowed is True and first.deduplicated is False
    dup = _call_det(store, config, {"n": 1}, limit=1)
    assert dup.allowed is True and dup.deduplicated is True


# --- evaluate_assisted ------------------------------------------------------


def test_assisted_disabled_mirrors_had_providers() -> None:
    config = make_config(enabled=False)
    limits = make_limits()
    assert _call_assisted(InMemoryAbuseStore(), config, limits, {"n": 1}).allow_ai is True
    assert (
        _call_assisted(InMemoryAbuseStore(), config, limits, {"n": 1}, had_providers=False).allow_ai
        is False
    )


def test_assisted_duplicate_degrades_deduplicated() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    limits = make_limits(ai_requests_per_ip_per_day=5)
    assert _call_assisted(store, config, limits, {"n": 1}).allow_ai is True
    dup = _call_assisted(store, config, limits, {"n": 1})
    assert dup.allow_ai is False and dup.deduplicated is True
    assert dup.forced_reason == REASON_DEDUPLICATED


def test_assisted_absolute_ceiling_returns_429() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    limits = make_limits(deterministic_requests_per_ip_per_day=0, ai_requests_per_ip_per_day=5)
    decision = _call_assisted(store, config, limits, {"n": 1})
    assert decision.rate_limited is True and decision.retry_after > 0


def test_assisted_no_providers_does_not_gate() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    limits = make_limits()
    decision = _call_assisted(store, config, limits, {"n": 1}, had_providers=False)
    assert decision.allow_ai is False
    assert decision.degrade is False
    assert decision.forced_reason is None


def test_assisted_kill_switch_flag_forces_degrade() -> None:
    store = InMemoryAbuseStore()
    store.set_flag(AI_KILL_SWITCH_FLAG, True, FIXED_NOW)
    config = make_config()
    limits = make_limits(ai_requests_per_ip_per_day=5)
    decision = _call_assisted(store, config, limits, {"n": 1})
    assert decision.allow_ai is False and decision.degrade is True
    assert decision.forced_reason == REASON_AI_KILL_SWITCH


def test_assisted_kill_switch_env_override_forces_degrade() -> None:
    store = InMemoryAbuseStore()
    config = make_config(ai_kill_switch_override=True)
    limits = make_limits(ai_requests_per_ip_per_day=5)
    decision = _call_assisted(store, config, limits, {"n": 1})
    assert decision.forced_reason == REASON_AI_KILL_SWITCH


def test_assisted_within_free_threshold_allows_ai() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    limits = make_limits(ai_requests_per_ip_per_day=3)
    assert _call_assisted(store, config, limits, {"n": 1}).allow_ai is True


def test_assisted_beyond_free_without_captcha_degrades_quota_exhausted() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    limits = make_limits(ai_requests_per_ip_per_day=0, require_captcha=False)
    decision = _call_assisted(store, config, limits, {"n": 1})
    assert decision.allow_ai is False and decision.degrade is True
    assert decision.forced_reason == REASON_AI_QUOTA_EXHAUSTED


def test_assisted_beyond_free_with_captcha_requires_pow() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    limits = make_limits(ai_requests_per_ip_per_day=0, require_captcha=True)
    decision = _call_assisted(store, config, limits, {"n": 1})
    assert decision.pow_required is True
    assert decision.forced_reason == REASON_POW_REQUIRED


def test_assisted_beyond_free_with_valid_pow_allows_ai() -> None:
    store = InMemoryAbuseStore()
    config = make_config()
    limits = make_limits(ai_requests_per_ip_per_day=0, require_captcha=True)
    request = make_request()
    ip_hash = service.client_ip_hash(config, request)
    issued = powmod.issue_challenge(store, config, ip_hash, FIXED_NOW)
    nonce = powmod.solve(issued.token, issued.difficulty)

    decision = service.evaluate_assisted(
        store,
        config,
        limits,
        request,
        {"n": 1},
        had_providers=True,
        pow_token=issued.token,
        pow_nonce=nonce,
        now=FIXED_NOW,
    )
    assert decision.allow_ai is True and decision.pow_ok is True
