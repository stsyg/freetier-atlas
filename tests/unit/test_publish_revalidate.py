"""Unit tests for deterministic quota re-validation (F005 slice 2)."""

from __future__ import annotations

import sys
import unicodedata
from decimal import Decimal

import pytest
from app.publish.revalidate import (
    NON_QUOTA_FIELDS,
    invalid_optional_offer_fields,
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


@pytest.mark.parametrize(
    ("raw", "amount", "unit", "reset_period"),
    [
        ("10K", Decimal("10000"), None, None),
        ("2.5K", Decimal("2500.0"), None, None),
        ("1M/month", Decimal("1000000"), None, "month"),
        ("3B requests", Decimal("3000000000"), "requests", None),
        ("First 10K", Decimal("10000"), None, None),
        ("Up to 1M", Decimal("1000000"), None, None),
        ("first 100", Decimal("100"), None, None),
    ],
)
def test_parse_compact_decimal_count_suffixes(
    raw: str,
    amount: Decimal,
    unit: str | None,
    reset_period: str | None,
) -> None:
    q = parse_quantity(raw)
    assert q.raw == raw
    assert q.amount == amount
    assert q.unit == unit
    assert q.reset_period == reset_period


@pytest.mark.parametrize(
    ("raw", "unit"),
    [
        ("10ms", "ms"),
        ("10GB", "GB"),
        ("10Mbps", "Mbps"),
        ("10Kbps", "Kbps"),
        ("10kB", "kB"),
        ("10MBps", "MBps"),
    ],
)
def test_parse_unambiguous_compact_ordinary_units(raw: str, unit: str) -> None:
    q = parse_quantity(raw)
    assert q.amount == Decimal("10")
    assert q.unit == unit
    assert q.reset_period is None


@pytest.mark.parametrize(
    "raw",
    [
        "10m",
        "10 m",
        "10 k",
        "10 K",
        "10 M",
        "10 B",
        "10 K, then paid",
        "10 K.",
        "10 K)",
        "10.K",
        "10\u200bK",
        "10\u00adK",
        "10\u2060K",
        "10\u2013K",
        "10MiB",
        "10KiB",
        "10T",
        "10Q",
        "1MM",
        "-10K",
        "+10K",
        "\u221210K",
        "\uff0b10K",
        "\u00b110K",
        "\ufe6210K",
        "\ufe6310K",
        "\u279510K",
        "\u279610K",
        "\u201410K",
        "\u203010K",
        "1e3",
        "1E3",
        "10K10",
        "10K+",
        "1,5K",
        "10MB",
        "10KB",
        "10MiB",
        "10KiB",
        "10GiB",
        "10KK",
    ],
)
def test_parse_unsupported_compact_magnitudes_fails_closed(raw: str) -> None:
    q = parse_quantity(raw)
    assert q.raw == raw
    assert q.amount is None
    assert q.unit is None
    assert q.reset_period is None


@pytest.mark.parametrize(
    "raw",
    [
        "Version 2: 10K",
        "2026-08-07 10K",
        "1.2.3 10K",
        "3-5 requests",
        "10K to 20K",
    ],
)
def test_multiple_numeric_tokens_fail_closed(raw: str) -> None:
    q = parse_quantity(raw)
    assert q.raw == raw
    assert q.amount is None
    assert q.unit is None
    assert q.reset_period is None


def test_one_numeric_token_with_qualifier_remains_supported() -> None:
    assert parse_quantity("First 10K").amount == Decimal("10000")


@pytest.mark.parametrize("whitespace", [" ", "\t", "\u00a0", "\u2009", "\u2003"])
@pytest.mark.parametrize("sign", ["+", "-", "\ufe62", "\u2212", "\u2795", "\u2796"])
def test_whitespace_separated_signs_fail_closed(sign: str, whitespace: str) -> None:
    q = parse_quantity(f"{sign}{whitespace}10K")
    assert q.amount is None
    assert q.unit is None
    assert q.reset_period is None


def test_unicode_named_sign_variants_fail_closed() -> None:
    sign_variants = {
        chr(codepoint)
        for codepoint in range(sys.maxunicode + 1)
        if (
            unicodedata.category(chr(codepoint)) == "Pd"
            or any(
                marker in unicodedata.name(chr(codepoint), "")
                for marker in ("PLUS", "MINUS", "HYPHEN", "DASH")
            )
        )
        and not chr(codepoint).isspace()
        and not chr(codepoint).isalnum()
    }
    assert {"+", "-", "\ufe62", "\u2212", "\u2795", "\u2796"} <= sign_variants
    assert all(
        parse_quantity(f"{sign}\u200b\u2060\u200310K").amount is None for sign in sign_variants
    )


def test_sign_semantics_precede_alphanumeric_qualifier_boundary() -> None:
    sign_like_alphanumeric = next(
        chr(codepoint)
        for codepoint in range(sys.maxunicode + 1)
        if chr(codepoint).isalnum()
        and any(
            marker in unicodedata.name(chr(codepoint), "")
            for marker in ("PLUS", "MINUS", "HYPHEN", "DASH")
        )
    )
    assert parse_quantity(f"{sign_like_alphanumeric} 10K").amount is None


@pytest.mark.parametrize(
    "raw",
    [
        "+\u200b 10K",
        "-\u2060 10K",
        "+\u200e 10K",
        "-\u200f 10K",
        "+\u202a\u202c 10K",
        "-\ufeff 10K",
        "\u2012 10K",
        "\u2013 10K",
        "\u2014 10K",
        "+/- 10K",
        "-/+ 10K",
        "++ 10K",
        "-- 10K",
        "\u00b1 10K",
        "+ : 10K",
        "- ( 10K",
        "+\u200b\u2060:\u200e(\u200310K",
        "+\u0301 10K",
        "-\u20e0 10K",
        "+\ufe0e 10K",
        "-\ufe0f 10K",
        "+\u0301\u20e0\ufe0f 10K",
        "+\u0301\u200b\u2060\u200e 10K",
        "First - 10K",
    ],
)
def test_trailing_prefix_sign_token_fails_closed(raw: str) -> None:
    q = parse_quantity(raw)
    assert q.amount is None
    assert q.unit is None
    assert q.reset_period is None


@pytest.mark.parametrize(
    "raw",
    [": 10K", "( 10K", "~ 10K", "First: 10K", "pre-paid First: 10K"],
)
def test_separated_qualifier_punctuation_remains_supported(raw: str) -> None:
    assert parse_quantity(raw).amount == Decimal("10000")


@pytest.mark.parametrize(
    "token_material",
    [
        "\u0301",  # Mn
        "\u0903",  # Mc
        "\u20e0",  # Me
        "\u0001",  # Cc
        "\u200b",  # Cf
        "\ud800",  # Cs
        "\ue000",  # Co
        "\u0378",  # Cn
        "\u0020",  # Zs
        "\u2028",  # Zl
        "\u2029",  # Zp
    ],
)
def test_non_word_unicode_categories_cannot_hide_a_sign(token_material: str) -> None:
    assert parse_quantity(f"+{token_material} 10K").amount is None


@pytest.mark.parametrize("combining_mark", [chr(codepoint) for codepoint in range(0x0300, 0x0314)])
def test_sampled_combining_marks_cannot_hide_a_sign(combining_mark: str) -> None:
    assert parse_quantity(f"+{combining_mark} 10K").amount is None


@pytest.mark.parametrize(
    ("raw", "amount", "unit"),
    [
        ("10ms", Decimal("10"), "ms"),
        ("10GB", Decimal("10"), "GB"),
        ("10Mbps", Decimal("10"), "Mbps"),
        ("10Kbps", Decimal("10"), "Kbps"),
        ("10kB", Decimal("10"), "kB"),
        ("10MBps", Decimal("10"), "MBps"),
        ("10MB", None, None),
        ("10KB", None, None),
        ("10KiB", None, None),
        ("10MiB", None, None),
        ("10GiB", None, None),
        ("10T", None, None),
        ("10Q", None, None),
        ("10MM", None, None),
        ("10KK", None, None),
        ("10Kfoo", None, None),
        ("10Mfoo", None, None),
        ("10Brequests", None, None),
        ("10Krequests", None, None),
    ],
)
def test_compact_unit_token_boundary(
    raw: str,
    amount: Decimal | None,
    unit: str | None,
) -> None:
    q = parse_quantity(raw)
    assert q.amount == amount
    assert q.unit == unit


@pytest.mark.parametrize(
    ("raw", "amount", "unit", "reset_period"),
    [
        ("100,000/day", Decimal("100000"), None, "day"),
        ("128 MB", Decimal("128"), "MB", None),
        ("50%", Decimal("50"), "%", None),
        ("0.125", Decimal("0.125"), None, None),
        ("1 build at a time", Decimal("1"), "build at a time", None),
    ],
)
def test_parse_existing_quantity_behaviour_is_preserved(
    raw: str,
    amount: Decimal,
    unit: str | None,
    reset_period: str | None,
) -> None:
    q = parse_quantity(raw)
    assert q.amount == amount
    assert q.unit == unit
    assert q.reset_period == reset_period


def test_compact_suffix_is_not_retained_as_a_unit() -> None:
    q = parse_quantity("3B requests")
    assert q.amount == Decimal("3000000000")
    assert q.unit == "requests"


def test_metric_reset_fallback_is_preserved_for_compact_suffix() -> None:
    q = parse_quantity("1M", metric="requests_per_month")
    assert q.amount == Decimal("1000000")
    assert q.reset_period == "month"


def test_unparseable_value_yields_none_never_guessed() -> None:
    q = parse_quantity("unlimited", metric="whatever")
    assert q.amount is None
    assert q.has_number is False


def test_empty_value_is_none() -> None:
    assert parse_quantity(None).amount is None
    assert parse_quantity("").amount is None
    assert parse_quantity("   ").amount is None


def test_revalidate_skips_non_quota_fields() -> None:
    facts = {
        **WORKERS_FACTS,
        "eligibility": "Personal, non-commercial use only",
        "commercial_use_allowed": False,
        "personal_use_allowed": True,
    }
    result = revalidate_quotas(facts, exhaustion_behaviour="request_rejected")
    metrics = {q.metric for q in result.quotas}
    assert metrics.isdisjoint(NON_QUOTA_FIELDS)
    assert "requests_per_day" in metrics
    assert "service" not in metrics
    assert "eligibility" not in metrics
    assert "commercial_use_allowed" not in metrics
    assert "personal_use_allowed" not in metrics


class _ProtocolImpostor:
    def __bool__(self) -> bool:
        raise AssertionError("validation must not invoke truthiness")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("validation must not invoke equality")

    def __str__(self) -> str:
        raise AssertionError("validation must not invoke string coercion")


class _StringCoercionImpostor:
    def __str__(self) -> str:
        raise AssertionError("validation must not invoke string coercion")


@pytest.mark.parametrize(
    "facts",
    [
        {},
        {"eligibility": None},
        {"eligibility": "Personal use only"},
        {"eligibility": "  Personal use only  "},
        {"commercial_use_allowed": None},
        {"commercial_use_allowed": False},
        {"commercial_use_allowed": True},
        {"personal_use_allowed": None},
        {"personal_use_allowed": False},
        {"personal_use_allowed": True},
    ],
)
def test_optional_offer_field_validator_accepts_only_valid_structured_values(facts: dict) -> None:
    assert invalid_optional_offer_fields(facts) == ()


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("eligibility", ""),
        ("eligibility", " \t "),
        ("eligibility", 0),
        ("eligibility", True),
        ("eligibility", _StringCoercionImpostor()),
        ("eligibility", _ProtocolImpostor()),
        ("commercial_use_allowed", "false"),
        ("commercial_use_allowed", 0),
        ("commercial_use_allowed", 1),
        ("commercial_use_allowed", _ProtocolImpostor()),
        ("personal_use_allowed", "true"),
        ("personal_use_allowed", 0),
        ("personal_use_allowed", 1),
        ("personal_use_allowed", _ProtocolImpostor()),
    ],
)
def test_optional_offer_field_validator_rejects_coercion_and_equality_impostors(
    field_name: str,
    value: object,
) -> None:
    assert invalid_optional_offer_fields({field_name: value}) == (field_name,)


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
    assert result.parsed_count == 0
    assert result.unparsed_fields == ("weird_metric",)


@pytest.mark.parametrize(
    "raw",
    [
        "10m",
        "10 m",
        "10 k",
        "10 K",
        "10 K, then paid",
        "10.K",
        "10\u200bK",
        "10\u00adK",
        "10MiB",
        "10T",
        "1MM",
        "-10K",
        "\u221210K",
        "\ufe6210K",
        "\u279510K",
        "1e3",
        "10K10",
        "1,5K",
        "+ 10K",
        "\ufe62\u200910K",
        "\u2796\t10K",
        "+\u200b\u2060:\u200e(\u200310K",
        "\u2014 10K",
        "10Kfoo",
        "10Brequests",
        "Version 2: 10K",
        "10K to 20K",
    ],
)
def test_unsupported_numeric_form_fails_deterministic_gate(raw: str) -> None:
    facts = {"service": "x", "offer_type": "always_free", "ambiguous_metric": raw}
    result = revalidate_quotas(facts, exhaustion_behaviour="unknown")
    assert result.unparsed_fields == ("ambiguous_metric",)
    assert result.quotas[0].amount is None
    assert result.deterministic is False


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
