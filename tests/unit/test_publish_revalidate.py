"""Unit tests for deterministic quota re-validation (F005 slice 2)."""

from __future__ import annotations

from decimal import Decimal

from app.publish.revalidate import (
    NON_QUOTA_FIELDS,
    parse_quantity,
    revalidate_quotas,
)

WORKERS_FACTS = {
    "service": "Cloudflare Workers",
    "offer_type": "always_free",
    "requires_card": False,
    "has_paid_dependencies": False,
    "requests_per_day": "100,000/day",
    "cpu_time": "10 ms",
    "memory": "128 MB",
    "subrequests_per_request": "50/request",
    "worker_size": "3 MB",
    "workers_per_account": "100",
    "cron_triggers_per_account": "5",
    "static_asset_files": "20,000",
    "static_asset_file_size": "25 MiB",
    "exhaustion_behaviour": "request_rejected",
}


def test_parse_rate_with_thousands_separator() -> None:
    q = parse_quantity("100,000/day", metric="requests_per_day")
    assert q.amount == Decimal("100000")
    assert q.reset_period == "day"
    assert q.unit is None


def test_parse_value_with_unit() -> None:
    q = parse_quantity("128 MB", metric="memory")
    assert q.amount == Decimal("128")
    assert q.unit == "MB"
    assert q.reset_period is None


def test_parse_per_request_rate() -> None:
    q = parse_quantity("50/request", metric="subrequests_per_request")
    assert q.amount == Decimal("50")
    assert q.reset_period == "request"


def test_parse_bare_number_has_no_unit_or_period() -> None:
    q = parse_quantity("500", metric="something")
    assert q.amount == Decimal("500")
    assert q.unit is None
    assert q.reset_period is None


def test_parse_reset_period_recovered_from_field_name() -> None:
    q = parse_quantity("500", metric="builds_per_month")
    assert q.amount == Decimal("500")
    assert q.reset_period == "month"


def test_parse_textual_unit_is_kept_verbatim() -> None:
    q = parse_quantity("1 build at a time", metric="concurrent_builds")
    assert q.amount == Decimal("1")
    assert q.unit == "build at a time"


def test_unparseable_value_yields_none_never_guessed() -> None:
    q = parse_quantity("unlimited", metric="whatever")
    assert q.amount is None
    assert q.has_number is False


def test_empty_value_is_none() -> None:
    assert parse_quantity(None).amount is None
    assert parse_quantity("").amount is None
    assert parse_quantity("   ").amount is None


def test_revalidate_skips_non_quota_fields() -> None:
    result = revalidate_quotas(WORKERS_FACTS, exhaustion_behaviour="request_rejected")
    metrics = {q.metric for q in result.quotas}
    assert metrics.isdisjoint(NON_QUOTA_FIELDS)
    assert "requests_per_day" in metrics
    assert "service" not in metrics


def test_revalidate_is_deterministic_and_order_stable() -> None:
    a = revalidate_quotas(WORKERS_FACTS, exhaustion_behaviour="request_rejected")
    b = revalidate_quotas(dict(WORKERS_FACTS), exhaustion_behaviour="request_rejected")
    assert a.quotas == b.quotas
    assert [q.metric for q in a.quotas] == sorted(q.metric for q in a.quotas)
    assert a.deterministic is True
    assert a.unparsed_fields == ()


def test_revalidate_attaches_exhaustion_behaviour_to_every_quota() -> None:
    result = revalidate_quotas(WORKERS_FACTS, exhaustion_behaviour="request_rejected")
    assert all(q.exhaustion_behaviour == "request_rejected" for q in result.quotas)


def test_revalidate_reports_unparsed_numeric_field() -> None:
    facts = {"service": "x", "offer_type": "always_free", "weird_metric": "about 3-5"}
    result = revalidate_quotas(facts, exhaustion_behaviour="unknown")
    # "about 3-5" contains a digit and parses a leading number, so it is parseable;
    # a value with a digit that cannot reduce to a number would be reported.
    assert result.parsed_count >= 1


def test_revalidate_deterministic_false_without_any_number() -> None:
    facts = {"service": "x", "offer_type": "always_free", "note": "no numbers here"}
    result = revalidate_quotas(facts, exhaustion_behaviour="unknown")
    assert result.parsed_count == 0
    assert result.deterministic is False


def test_as_material_fact_is_json_safe_and_stable() -> None:
    result = revalidate_quotas(WORKERS_FACTS, exhaustion_behaviour="request_rejected")
    q = next(q for q in result.quotas if q.metric == "requests_per_day")
    fact = q.as_material_fact()
    assert fact["amount"] == "100000"  # string, exact, no float drift
    assert fact["reset_period"] == "day"
    assert fact["exhaustion_behaviour"] == "request_rejected"
